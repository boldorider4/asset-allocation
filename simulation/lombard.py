import matplotlib.pyplot as plt
import numpy as np

# ==========================================
# SAMPLE PORTFOLIO DICT STRUCTURE:
# ==========================================
# X_years = 2
# total_months = 24
#
# sample_unleveraged = {
#     "equity": ([1.005, 1.00467, 1.0075, ...], .8) # size 24 months,
#     "commodities": ([1.002, 1.0015, 1.003, ...], .1) # size 24 months,
#     "gold": ([1.001, 1.0012, 1.0008, ...], .1) # size 24 months
# }
#
# sample_leveraged = {
#     "equity": ([1.004, 1.0035, 1.006, ...], .7) # size 24 months,
#     "bonds": ([1.001, 1.0011, 1.0009, ...], .1) # size 24 months,
#     "gold": ([1.001, 1.0012, 1.0008, ...], .1) # size 24 months,
#     "commodities": ([1.001, 1.0015, 1.002, ...], .1) # size 24 months
# }
#
# sample_euribor = [0.0245, 0.0245, 0.0246, ...] # size 24 months
# ==========================================

def _weighted_monthly_returns(portfolio_dict, rebalance_months=12):
    """Blend per-asset monthly gross return series into a single portfolio series.

    Each dict value is a ``(returns, weight)`` pair where ``weight`` is the asset's
    target share of the portfolio (0.5 -> 50%). Targets are normalized so a portfolio
    that does not sum to exactly 1.0 still produces a valid weighted average.

    Between rebalance dates the actual weights drift with performance: outperformers
    become overweight and compound on a larger base. Every ``rebalance_months`` the
    overweight assets are sold and the underweight ones bought to restore the target
    allocation. ``rebalance_months=1`` rebalances every month; ``None`` or ``0`` lets
    the portfolio drift for the whole horizon (buy and hold).
    """
    series = []
    targets = []
    for returns, weight in portfolio_dict.values():
        series.append(np.asarray(returns, dtype=float))
        targets.append(float(weight))

    returns_matrix = np.vstack(series)
    targets = np.asarray(targets, dtype=float)
    targets = targets / targets.sum()

    weights = targets.copy()
    portfolio_returns = np.empty(returns_matrix.shape[1], dtype=float)

    for month in range(returns_matrix.shape[1]):
        month_returns = returns_matrix[:, month]
        # Value-weighted blend of this month's asset returns.
        growth = float(weights @ month_returns)
        portfolio_returns[month] = growth

        # Each asset's share of the (now larger or smaller) portfolio after the month.
        weights = weights * month_returns / growth

        if rebalance_months and (month + 1) % rebalance_months == 0:
            weights = targets.copy()

    return portfolio_returns


def compare_portfolios(
    unleveraged_dict, leveraged_dict, euribor_array, years, ltv=0.5, spread=0.0125, initial_equity=100000.0,
    rebalance_months=12,
):
    total_months = 12 * years
    if len(euribor_array) != total_months:
        raise ValueError(f"Euribor array length {len(euribor_array)} != {total_months}")

    # 1. Calculate Unleveraged Portfolio Path
    unlev_monthly_returns = _weighted_monthly_returns(unleveraged_dict, rebalance_months)
    if len(unlev_monthly_returns) != total_months:
        raise ValueError(f"Unleveraged returns length {len(unlev_monthly_returns)} != {total_months}")

    unlev_capital = [initial_equity]
    for r in unlev_monthly_returns:
        unlev_capital.append(unlev_capital[-1] * r)

    # 2. Calculate Leveraged Portfolio Path
    # LTV determines loan size relative to total equity. LTV = L / (E + L)
    loan_principal = initial_equity * ltv / (1 - ltv)
    lev_initial_portfolio = initial_equity + loan_principal

    lev_monthly_returns = _weighted_monthly_returns(leveraged_dict, rebalance_months)
    if len(lev_monthly_returns) != total_months:
        raise ValueError(f"Leveraged returns length {len(lev_monthly_returns)} != {total_months}")
    
    lev_capital = [lev_initial_portfolio]
    cumulative_liability = [loan_principal] # Track loan being paid off

    for m in range(total_months):
        # Apply monthly portfolio return
        port_val = lev_capital[-1] * lev_monthly_returns[m]
        lev_capital.append(port_val)

        # Calculate monthly interest rate (Euribor + spread annualized / 12)
        annual_rate = euribor_array[m] + spread
        monthly_interest = loan_principal * (annual_rate / 12.0)

        # Let's assume interest is paid monthly (cash outflow) and principal remains constant until final payback
        # Let's plot cumulative liability flow or monthly spend, which will then be subtracted from the portfolio value
        # to get the net value of the portfolio.
        cumulative_liability.append(cumulative_liability[-1] + monthly_interest)

    # ROI of unleveraged vs. leveraged
    final_unlev_roi = (unlev_capital[-1] - initial_equity) / initial_equity
    net_lev_capital = np.asarray(lev_capital) - np.asarray(cumulative_liability)
    final_lev_roi = (net_lev_capital[-1] - initial_equity) / initial_equity
    # STD of unleveraged vs. leveraged
    unlev_std = np.std(unlev_capital) / initial_equity
    lev_std = np.std(net_lev_capital) / initial_equity

    # # 3. Draw Charts
    # fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    # months_axis = range(total_months + 1)

    # # Unleveraged Chart
    # ax1.plot(months_axis, unlev_capital, label="Unleveraged Portfolio Value", color="blue")
    # ax1.set_title("Unleveraged Case")
    # ax1.set_xlabel("Months")
    # ax1.set_ylabel("Capital (€)")
    # ax1.legend()
    # ax1.grid(True)

    # # Leveraged Chart
    # ax2.plot(months_axis, lev_capital, label="Leveraged Portfolio Value", color="green")
    # ax2.plot(months_axis, cumulative_liability, label="Cumulative Liability (Loan + Interest)", color="red", linestyle="--")
    # ax2.set_title("Leveraged Case")
    # ax2.set_xlabel("Months")
    # ax2.set_ylabel("Amount (€)")
    # ax2.legend()
    # ax2.grid(True)

    # plt.tight_layout()
    # plt.show()

    return {
        "unlev_roi": final_unlev_roi,
        "lev_roi": final_lev_roi,
        "unlev_std": unlev_std,
        "lev_std": lev_std,
        "unlev_portfolio": unlev_capital,
        "lev_portfolio": net_lev_capital.tolist(),
    }


if __name__ == "__main__":
    years = 10
    n_months = 12 * years

    def monthly_gross_returns(n, annual_mean, annual_vol, seed):
        """Independent monthly gross factors for one asset class.

        Calibrated so that compounding any 12 of them gives an annual gross return
        with mean ``1 + annual_mean`` and standard deviation ``annual_vol`` relative
        to that mean. With annual_mean=0.08 and annual_vol=0.16, a typical year lands
        in 1.08 +/- 1.08*0.16 (roughly -9% to +25%), while tail years reach -20% or
        +40%. Months are genuinely independent, so a decade can still end down.
        """
        rng = np.random.default_rng(seed)
        monthly_mean = (1.0 + annual_mean) ** (1.0 / 12.0)
        monthly_sd = monthly_mean * annual_vol / np.sqrt(12.0)
        return rng.normal(monthly_mean, monthly_sd, n)

    def sample_euribor_3m(n):
        knots_x = np.array([0, 12, 18, 36, 60, 72, 96, 119], dtype=float)
        knots_y = np.array([0.027, 0.02, 0.024, 0.032, 0.0197, 0.017, 0.0167, 0.0155])
        return np.interp(np.arange(n), knots_x, knots_y)

    import time

    n_sims = 1000
    initial_equity = 100000.0
    sample_euribor = sample_euribor_3m(n_months)
    seed_base = time.time_ns()

    unlev_rois = np.empty(n_sims)
    lev_rois = np.empty(n_sims)
    unlev_stds = np.empty(n_sims)
    lev_stds = np.empty(n_sims)

    for i in range(n_sims):
        s = seed_base + i * 5
        equity = monthly_gross_returns(n_months, 0.07, 0.15, seed=s)
        commodities = monthly_gross_returns(n_months, 0.03, 0.05, seed=s + 1)
        gold = monthly_gross_returns(n_months, 0.05, 0.08, seed=s + 2)
        bonds = monthly_gross_returns(n_months, 0.035, 0.08, seed=s + 3)
        reit = monthly_gross_returns(n_months, 0.04, 0.08, seed=s + 4)

        sample_unleveraged = {
            "equity": (equity, 0.9),
            "commodities": (commodities, 0.05),
            "gold": (gold, 0.05),
        }
        sample_leveraged = {
            "equity": (equity, 0.5),
            "bonds": (bonds, 0.3),
            "gold": (gold, 0.08),
            "commodities": (commodities, 0.06),
            "reit": (reit, 0.06),
        }
        result = compare_portfolios(
            sample_unleveraged,
            sample_leveraged,
            sample_euribor,
            years=years,
            initial_equity=initial_equity,
        )
        unlev_rois[i] = result["unlev_roi"]
        lev_rois[i] = result["lev_roi"]
        unlev_stds[i] = result["unlev_std"]
        lev_stds[i] = result["lev_std"]

    print(
        f"Unlevered avg ROI {unlev_rois.mean() * 100:.2f}%, "
        f"total STD {unlev_rois.std() * 100:.2f}% "
        f"(avg path STD {unlev_stds.mean() * 100:.2f}%)"
    )
    print(
        f"Leveraged avg ROI {lev_rois.mean() * 100:.2f}%, "
        f"total STD {lev_rois.std() * 100:.2f}% "
        f"(avg path STD {lev_stds.mean() * 100:.2f}%)"
    )
