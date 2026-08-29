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

def _weighted_monthly_returns(portfolio_dict):
    """Blend per-asset monthly gross return series into a single portfolio series.

    Each dict value is a ``(returns, weight)`` pair where ``weight`` is the asset's
    share of the portfolio (0.5 -> 50%). Weights are normalized so a portfolio that
    does not sum to exactly 1.0 still produces a valid weighted average.
    """
    series = []
    weights = []
    for returns, weight in portfolio_dict.values():
        series.append(np.asarray(returns, dtype=float))
        weights.append(float(weight))

    return np.average(np.vstack(series), axis=0, weights=weights)


def compare_portfolios(
    unleveraged_dict, leveraged_dict, euribor_array, years, ltv=0.5, spread=0.0125, initial_equity=100000.0
):
    total_months = 12 * years
    if len(euribor_array) != total_months:
        raise ValueError(f"Euribor array length {len(euribor_array)} != {total_months}")

    # 1. Calculate Unleveraged Portfolio Path
    unlev_monthly_returns = _weighted_monthly_returns(unleveraged_dict)
    if len(unlev_monthly_returns) != total_months:
        raise ValueError(f"Unleveraged returns length {len(unlev_monthly_returns)} != {total_months}")

    unlev_capital = [initial_equity]
    for r in unlev_monthly_returns:
        unlev_capital.append(unlev_capital[-1] * r)

    # 2. Calculate Leveraged Portfolio Path
    # LTV determines loan size relative to total equity. LTV = L / (E + L)
    loan_principal = initial_equity * ltv / (1 - ltv)
    lev_initial_portfolio = initial_equity + loan_principal

    lev_monthly_returns = _weighted_monthly_returns(leveraged_dict)
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

    final_unlev_roi = (unlev_capital[-1] - initial_equity) / initial_equity
    final_lev_net_val = np.asarray(lev_capital) - np.asarray(cumulative_liability)
    final_lev_roi = (final_lev_net_val[-1] - initial_equity) / initial_equity

    # 3. Draw Charts
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    months_axis = range(total_months + 1)

    # Unleveraged Chart
    ax1.plot(months_axis, unlev_capital, label="Unleveraged Portfolio Value", color="blue")
    ax1.set_title("Unleveraged Case")
    ax1.set_xlabel("Months")
    ax1.set_ylabel("Capital (€)")
    ax1.legend()
    ax1.grid(True)

    # Leveraged Chart
    ax2.plot(months_axis, lev_capital, label="Leveraged Portfolio Value", color="green")
    ax2.plot(months_axis, cumulative_liability, label="Cumulative Liability (Loan + Interest)", color="red", linestyle="--")
    ax2.set_title("Leveraged Case")
    ax2.set_xlabel("Months")
    ax2.set_ylabel("Amount (€)")
    ax2.legend()
    ax2.grid(True)

    plt.tight_layout()
    plt.show()

    return {
        "Unleveraged Final ROI": f"{final_unlev_roi * 100:.2f}%",
        "Leveraged Final Net ROI": f"{final_lev_roi * 100:.2f}%",
        "Unleveraged Portfolio": unlev_capital,
        "Leveraged Portfolio": final_lev_net_val.tolist(),
    }
    