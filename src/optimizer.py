"""
多目的最適化エンジン

1. セグメント別価格モデル  - 間取り種別ごとにモデル分離（R²改善）
2. TCO計算                - 2年・5年の総支払額を計算
3. 駅別コスパ効率         - どの駅が通勤×家賃で最も効率的か
4. Paretoフロンティア      - 重み付けに依存しない純粋な最適集合
5. 感度分析               - 重みを変えてもランキングが安定するか
"""

import logging
import statistics
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 間取りのセグメント分類
SEGMENT_MAP = {
    "ワンルーム": "small", "1K": "small", "1DK": "small",
    "1LDK": "medium", "2K": "medium", "2DK": "medium",
    "2LDK": "large", "3K": "large", "3DK": "large", "3LDK": "large",
}


class PropertyOptimizer:

    def __init__(self):
        self._segment_models: Dict[str, dict] = {}   # segment -> {coef, mean, std}

    # ──────────────────────────────────────────────
    # 1. セグメント別価格モデル
    # ──────────────────────────────────────────────

    def fit_segment_models(self, properties: list) -> dict:
        """間取り別に分けて価格モデルを学習し、精度を比較する。"""
        segments: Dict[str, list] = {}
        for p in properties:
            seg = SEGMENT_MAP.get(p.floor_plan, "other")
            segments.setdefault(seg, []).append(p)

        results = {}
        for seg, props in segments.items():
            valid = [p for p in props if p.rent > 0 and p.area > 0]
            if len(valid) < 8:
                continue
            model_info = self._fit_one_segment(valid)
            if model_info:
                model_info["count"] = len(valid)
                self._segment_models[seg] = model_info
                results[seg] = {
                    "count": len(valid),
                    "mape_pct": round(model_info.get("mape", 0) * 100, 1),
                    "r2": round(model_info.get("r2", 0), 3),
                    "avg_rent": round(statistics.mean(p.rent for p in valid)),
                }

        return results

    def predict_fair_rent(self, prop) -> Optional[float]:
        """セグメント別モデルで適正家賃を予測する。"""
        seg = SEGMENT_MAP.get(prop.floor_plan, "other")
        m = self._segment_models.get(seg) or self._segment_models.get("other")
        if not m:
            return None
        return self._predict_one(prop, m)

    def value_gap_pct(self, prop) -> Optional[float]:
        """適正家賃との乖離率 (正=割安, 負=割高) をパーセントで返す。"""
        fair = self.predict_fair_rent(prop)
        if not fair or fair <= 0:
            return None
        return (fair - prop.rent) / fair * 100

    # ──────────────────────────────────────────────
    # 2. TCO（総所有コスト）計算
    # ──────────────────────────────────────────────

    @staticmethod
    def calc_tco(prop, years: int = 2,
                 moving_cost: int = 80000,
                 agent_fee_months: float = 1.0,
                 renewal_fee_months: float = 1.0) -> dict:
        """
        N年間の総支払額 (TCO) を計算する。

        初期費用: 敷金 + 礼金 + 仲介手数料 + 引越し費
        月次費用: 家賃 + 管理費
        更新費用: 2年毎に更新料（通常1ヶ月分）
        """
        monthly = prop.total_monthly
        initial = (
            prop.deposit
            + prop.key_money
            + int(prop.rent * agent_fee_months)
            + moving_cost
        )
        renewals = (years // 2) * int(prop.rent * renewal_fee_months)
        total = initial + monthly * 12 * years + renewals

        return {
            "years": years,
            "initial": initial,
            "monthly": monthly,
            "monthly_total": monthly * 12 * years,
            "renewals": renewals,
            "tco": total,
            "monthly_equiv": total // (years * 12),   # 月換算
        }

    # ──────────────────────────────────────────────
    # 3. 駅別コスパ効率
    # ──────────────────────────────────────────────

    @staticmethod
    def station_efficiency(properties: list) -> List[dict]:
        """
        駅ごとに「家賃中央値 × 通勤時間 / 面積中央値」を計算する。
        値が低いほど「広くて安くて近い」効率的な駅。
        """
        stations: Dict[str, list] = {}
        for p in properties:
            ns = p.nearest_station
            if not ns:
                continue
            stations.setdefault(ns.name, []).append(p)

        rows = []
        for station, props in stations.items():
            valid = [p for p in props if p.commute_minutes and p.area > 0 and p.rent > 0]
            if len(valid) < 2:
                continue
            rents     = [p.rent for p in valid]
            areas     = [p.area for p in valid]
            commutes  = [p.commute_minutes for p in valid]
            med_rent  = statistics.median(rents)
            med_area  = statistics.median(areas)
            med_comm  = statistics.median(commutes)
            # 効率スコア = 円/m²/通勤分  (低いほど効率的)
            efficiency = (med_rent / med_area) * med_comm / 100
            rows.append({
                "station":        station,
                "count":          len(valid),
                "median_rent":    int(med_rent),
                "median_area":    round(med_area, 1),
                "median_commute": int(med_comm),
                "rent_per_m2":    round(med_rent / med_area),
                "efficiency":     round(efficiency, 1),
            })

        return sorted(rows, key=lambda r: r["efficiency"])

    # ──────────────────────────────────────────────
    # 4. Paretoフロンティア
    # ──────────────────────────────────────────────

    @staticmethod
    def pareto_frontier(properties: list) -> List:
        """
        多目的最適化のPareto最適物件を返す。

        最小化: 家賃合計, 通勤時間
        最大化: 面積, スコア

        いずれの軸でも「全て上位の物件が存在しない」物件がPareto最適。
        """
        if not properties:
            return []

        dominated = set()
        n = len(properties)

        def metrics(p):
            return (
                -p.total_monthly,                           # 高いほど悪（最小化）
                -(p.commute_minutes or 99),                 # 長いほど悪
                p.area,                                     # 大きいほど良（最大化）
                p.score or 0,                               # 高いほど良
            )

        for i in range(n):
            mi = metrics(properties[i])
            for j in range(n):
                if i == j:
                    continue
                mj = metrics(properties[j])
                # j が i を支配するか（全軸でjがiと同等以上かつ少なくとも1軸で優れる）
                if all(mj[k] >= mi[k] for k in range(len(mi))) and \
                   any(mj[k] >  mi[k] for k in range(len(mi))):
                    dominated.add(i)
                    break

        return [properties[i] for i in range(n) if i not in dominated]

    # ──────────────────────────────────────────────
    # 5. 感度分析
    # ──────────────────────────────────────────────

    @staticmethod
    def sensitivity_analysis(properties: list,
                              base_weights: dict,
                              top_n: int = 10) -> dict:
        """
        各重みを ±20%変化させたときにTOP Nランキングが
        どれだけ変動するかを計算する（ランキング安定性）。
        """
        if not properties:
            return {}

        def weighted_score(p, w):
            d = p.score_details or {}
            return sum(d.get(k, 0) * v for k, v in w.items())

        base_ranking = [p.id for p in sorted(
            properties, key=lambda p: weighted_score(p, base_weights), reverse=True
        )[:top_n]]

        results = {}
        for key in base_weights:
            variations = {}
            for delta in [-0.2, -0.1, +0.1, +0.2]:
                tweaked = dict(base_weights)
                tweaked[key] = max(0, tweaked[key] + delta)
                # 正規化
                total = sum(tweaked.values())
                tweaked = {k: v / total for k, v in tweaked.items()}

                new_ranking = [p.id for p in sorted(
                    properties,
                    key=lambda p: weighted_score(p, tweaked),
                    reverse=True
                )[:top_n]]

                # Kendall tau 的な順位変動（簡易版: 上位N中の一致率）
                overlap = len(set(base_ranking) & set(new_ranking))
                stability = overlap / top_n
                variations[f"{delta:+.0%}"] = round(stability, 2)

            results[key] = variations

        return results

    # ──────────────────────────────────────────────
    # internal
    # ──────────────────────────────────────────────

    def _fit_one_segment(self, props: list) -> Optional[dict]:
        try:
            import numpy as np

            X = [self._features(p) for p in props]
            y = [p.rent for p in props]
            X_arr = np.array(X, dtype=float)
            y_arr = np.array(y, dtype=float)

            mu  = X_arr.mean(axis=0)
            sg  = X_arr.std(axis=0) + 1e-8
            X_n = (X_arr - mu) / sg
            X_b = np.column_stack([np.ones(len(X_n)), X_n])

            coef, _, _, _ = np.linalg.lstsq(X_b, y_arr, rcond=None)

            # 残差評価（leave-one-out 簡易版）
            preds = X_b @ coef
            errors = np.abs(y_arr - preds) / y_arr
            mape = float(errors.mean())

            ss_res = float(((y_arr - preds) ** 2).sum())
            ss_tot = float(((y_arr - y_arr.mean()) ** 2).sum())
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

            return {
                "coef": coef.tolist(), "mean": mu.tolist(), "std": sg.tolist(),
                "mape": mape, "r2": r2,
            }
        except Exception as e:
            logger.warning(f"セグメントモデル構築失敗: {e}")
            return None

    def _predict_one(self, prop, model: dict) -> float:
        try:
            import numpy as np
            x = np.array(self._features(prop), dtype=float)
            xn = (x - np.array(model["mean"])) / np.array(model["std"])
            xb = np.concatenate([[1.0], xn])
            return float(np.dot(model["coef"], xb))
        except Exception:
            return 0.0

    @staticmethod
    def _features(prop) -> list:
        ns = prop.nearest_station
        return [
            prop.area,
            prop.age_years,
            ns.walk_minutes if ns else 15,
            prop.floor,
        ]
