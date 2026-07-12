"""掘り出し物検出エンジン

4つのシグナルを統合して掘り出し物スコア（0-100）を算出する。
  value_gap   35% - 適正家賃モデルとの乖離
  urgency     25% - 家主の焦り度（掲載日数・値下げ履歴）
  competition 20% - 競合物件数の少なさ
  contrarian  20% - バイアス反転（築古RC / 1階広め等）

ベンチマーク
  - 5-fold クロスバリデーションで MAPE / R² を計算
  - 掘り出し物発見率・シグナルカバレッジ率を報告
"""

import logging
import statistics
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BARGAIN_WEIGHTS = {
    "value_gap":   0.35,
    "urgency":     0.25,
    "competition": 0.20,
    "contrarian":  0.20,
}

STRUCTURE_RANK = {
    "RC": 4, "鉄筋コンクリート": 4, "SRC": 3,
    "ALC": 2, "鉄骨造": 2, "軽量鉄骨": 1, "木造": 0, "": 2,
}


class BargainEngine:
    def __init__(self):
        self._coef: Optional[list] = None   # 線形回帰係数
        self._X_mean: Optional[list] = None
        self._X_std: Optional[list] = None
        self._use_lgbm: bool = False
        self._lgbm_model = None

    # ──────────────────────── public ────────────────────────

    def fit_and_score(self, properties: list) -> list:
        """モデルを構築して全物件に掘り出し物スコアを付与する。"""
        if not properties:
            return properties

        self._fit(properties)
        comp_map = self._competition_map(properties)

        for prop in properties:
            score, details = self._score_one(prop, comp_map)
            prop.bargain_score = round(score, 1)
            prop.bargain_details = details

        return properties

    def benchmark(self, properties: list) -> dict:
        """ベンチマーク統計を返す（クロスバリデーション含む）。"""
        n = len(properties)
        if n == 0:
            return {}

        bargain_scores = [getattr(p, "bargain_score", None) for p in properties]
        bargain_scores = [s for s in bargain_scores if s is not None]

        scores = [p.score for p in properties if p.score is not None]
        bargains = [p for p in properties if getattr(p, "bargain_score", 0) >= 65]

        has_commute  = sum(1 for p in properties if p.commute_minutes is not None)
        has_wayback  = sum(1 for p in properties if getattr(p, "days_on_market", None) is not None)
        has_hazard   = sum(1 for p in properties if p.hazard_level not in (None, "unknown", ""))

        stats: dict = {
            "total":                n,
            "bargain_candidates":   len(bargains),
            "bargain_rate_pct":     round(len(bargains) / n * 100, 1),
            "avg_livability":       round(statistics.mean(scores), 1) if scores else 0,
            "median_livability":    round(statistics.median(scores), 1) if scores else 0,
            "avg_bargain_score":    round(statistics.mean(bargain_scores), 1) if bargain_scores else 0,
            "commute_coverage_pct": round(has_commute / n * 100, 1),
            "wayback_coverage_pct": round(has_wayback / n * 100, 1),
            "hazard_coverage_pct":  round(has_hazard  / n * 100, 1),
            "model_type":           "LightGBM" if self._use_lgbm else ("LinearReg" if self._coef else "なし"),
            "rent_dist":            self._rent_dist(properties),
        }

        # 価格予測ベンチマーク（クロスバリデーション）
        cv = self._cross_validate(properties)
        if cv:
            stats.update(cv)

        return stats

    # ──────────────────────── scoring ────────────────────────

    def _score_one(self, prop, comp_map: dict) -> Tuple[float, dict]:
        d: dict = {}
        d["value_gap"]   = self._score_value_gap(prop)
        d["urgency"]     = self._score_urgency(prop)
        d["competition"] = self._score_competition(prop, comp_map)
        d["contrarian"]  = self._score_contrarian(prop)
        total = sum(d[k] * BARGAIN_WEIGHTS[k] for k in d)
        return total, d

    def _score_value_gap(self, prop) -> float:
        if self._coef is None and self._lgbm_model is None:
            return 50.0
        predicted = self._predict(prop)
        if predicted <= 0:
            return 50.0
        ratio = prop.rent / predicted          # < 1.0 = 割安
        gap_pct = (1.0 - ratio) * 100         # % 安い
        # +20%安 → 100点、同額 → 50点、+20%高 → 0点
        return max(0.0, min(100.0, 50.0 + gap_pct * 2.5))

    def _score_urgency(self, prop) -> float:
        score = 20.0
        days = getattr(prop, "days_on_market", None)
        if days is not None:
            if days >= 90:   score += 70
            elif days >= 60: score += 50
            elif days >= 30: score += 30
            elif days >= 14: score += 10
        ph = getattr(prop, "price_history", None) or []
        if len(ph) >= 2:
            score += 20
        return min(100.0, score)

    def _score_competition(self, prop, comp_map: dict) -> float:
        ns = prop.nearest_station
        key = (ns.name if ns else "", prop.floor_plan)
        count = comp_map.get(key, 0)
        # 競合が少ない中で安い物件 = 穴場
        if count <= 1:   return 65
        elif count <= 3: return 80
        elif count <= 6: return 60
        elif count <= 12: return 45
        else:            return 30

    def _score_contrarian(self, prop) -> float:
        """一般人が避けるが実は価値がある条件を加点する。"""
        score = 20.0
        age = prop.age_years

        # 旧耐震基準（1981年以前 = 築44年以上）は大幅減点
        if age >= 44:
            return max(0.0, score - 35)

        # 築古RC/SRC（25〜43年）= 新耐震基準内・防音良好・安い
        if 25 <= age < 44 and prop.structure in ("RC", "鉄筋コンクリート", "SRC"):
            score += 45

        # 1階 + 広め = 専用庭・天井高の可能性
        if prop.floor == 1 and prop.area >= 40:
            score += 25

        # 高層（7F+）+ 比較的安い
        if prop.floor >= 7:
            score += 20

        # 駅遠（13分+）+ 広い = 自転車利用者には価値大
        ns = prop.nearest_station
        if ns and ns.walk_minutes >= 13 and prop.area >= 35:
            score += 20

        return min(100.0, score)

    # ──────────────────────── model ────────────────────────

    def _fit(self, properties: list):
        valid = [p for p in properties if p.rent > 0 and p.area > 0]
        if len(valid) < 15:
            logger.info("学習データ不足（<15件）: モデルなし")
            return

        # LightGBM が使えれば優先
        try:
            import lightgbm as lgb
            import numpy as np
            X = [self._features(p) for p in valid]
            y = [p.rent for p in valid]
            X_arr = np.array(X, dtype=float)
            y_arr = np.array(y, dtype=float)
            ds = lgb.Dataset(X_arr, label=y_arr)
            params = {"objective": "regression", "metric": "mape",
                      "num_leaves": 15, "n_estimators": 100, "verbose": -1}
            self._lgbm_model = lgb.train(params, ds, num_boost_round=100)
            self._use_lgbm = True
            logger.info(f"LightGBMモデル構築完了: {len(valid)}件")
            return
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"LightGBM失敗: {e}")

        # numpy 線形回帰（フォールバック）
        try:
            import numpy as np
            X = [self._features(p) for p in valid]
            y = [p.rent for p in valid]
            X_arr = np.array(X, dtype=float)
            y_arr = np.array(y, dtype=float)
            self._X_mean = X_arr.mean(axis=0).tolist()
            self._X_std  = (X_arr.std(axis=0) + 1e-8).tolist()
            X_norm = (X_arr - np.array(self._X_mean)) / np.array(self._X_std)
            X_b = np.column_stack([np.ones(len(X_norm)), X_norm])
            coef, _, _, _ = np.linalg.lstsq(X_b, y_arr, rcond=None)
            self._coef = coef.tolist()
            logger.info(f"線形回帰モデル構築完了: {len(valid)}件")
        except Exception as e:
            logger.warning(f"線形回帰失敗: {e}")

    def _predict(self, prop) -> float:
        try:
            if self._use_lgbm and self._lgbm_model:
                import numpy as np
                x = np.array([self._features(prop)], dtype=float)
                return float(self._lgbm_model.predict(x)[0])

            if self._coef:
                import numpy as np
                x = np.array(self._features(prop), dtype=float)
                x_norm = (x - np.array(self._X_mean)) / np.array(self._X_std)
                x_b = np.concatenate([[1.0], x_norm])
                return float(np.dot(self._coef, x_b))
        except Exception:
            pass
        return 0.0

    def _features(self, prop) -> list:
        ns = prop.nearest_station
        return [
            prop.area,
            prop.age_years,
            ns.walk_minutes if ns else 15,
            prop.floor,
            STRUCTURE_RANK.get(prop.structure, 2),
        ]

    # ──────────────────────── benchmark ────────────────────────

    def _cross_validate(self, properties: list, k: int = 5) -> dict:
        """5-fold クロスバリデーションで価格予測精度を計測する。"""
        valid = [p for p in properties if p.rent > 0 and p.area > 0]
        if len(valid) < k * 3:
            return {}

        try:
            import numpy as np

            n = len(valid)
            indices = list(range(n))
            fold_size = n // k
            mapes, r2s = [], []

            for fold in range(k):
                test_idx  = indices[fold * fold_size: (fold + 1) * fold_size]
                train_idx = [i for i in indices if i not in test_idx]

                train = [valid[i] for i in train_idx]
                test  = [valid[i] for i in test_idx]

                # fold用モデルを一時構築
                X_tr = np.array([self._features(p) for p in train], dtype=float)
                y_tr = np.array([p.rent for p in train], dtype=float)
                mu = X_tr.mean(axis=0)
                sg = X_tr.std(axis=0) + 1e-8
                X_b = np.column_stack([np.ones(len(X_tr)), (X_tr - mu) / sg])
                coef, _, _, _ = np.linalg.lstsq(X_b, y_tr, rcond=None)

                # テストセットで評価
                errors, preds, actuals = [], [], []
                for p in test:
                    x = np.array(self._features(p), dtype=float)
                    x_b = np.concatenate([[1.0], (x - mu) / sg])
                    pred = float(np.dot(coef, x_b))
                    if pred > 0:
                        errors.append(abs(p.rent - pred) / p.rent)
                        preds.append(pred)
                        actuals.append(p.rent)

                if errors:
                    mapes.append(statistics.mean(errors))
                if preds:
                    y_mean = statistics.mean(actuals)
                    ss_res = sum((a - p) ** 2 for a, p in zip(actuals, preds))
                    ss_tot = sum((a - y_mean) ** 2 for a in actuals)
                    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
                    r2s.append(r2)

            return {
                "cv_mape_pct":   round(statistics.mean(mapes) * 100, 1) if mapes else None,
                "cv_r2":         round(statistics.mean(r2s), 3) if r2s else None,
                "cv_folds":      k,
                "cv_train_size": len(valid) - fold_size,
            }
        except Exception as e:
            logger.warning(f"クロスバリデーション失敗: {e}")
            return {}

    # ──────────────────────── helpers ────────────────────────

    def _competition_map(self, properties: list) -> Dict[tuple, int]:
        counts: Dict[tuple, int] = {}
        for p in properties:
            ns = p.nearest_station
            key = (ns.name if ns else "", p.floor_plan)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _rent_dist(self, properties: list) -> dict:
        bands = {"~6万": 0, "6〜8万": 0, "8〜10万": 0, "10〜15万": 0, "15万〜": 0}
        for p in properties:
            r = p.rent
            if r < 60000:    bands["~6万"] += 1
            elif r < 80000:  bands["6〜8万"] += 1
            elif r < 100000: bands["8〜10万"] += 1
            elif r < 150000: bands["10〜15万"] += 1
            else:            bands["15万〜"] += 1
        return bands
