import matplotlib.pyplot as plt
import numpy as np

# Portfolio parameters
historical_price = 250000
transfer_value = 300000
fictional_cap_growth = .02
growth_spread = .045
years_before_transfer = 3
de_base_increase = historical_price * fictional_cap_growth**years_before_transfer
transfer_value = historical_price + (fictional_cap_growth + growth_spread)**years_before_transfer
annual_growth = 0.06
years = 25
de_tax = 0.26375
it_tax = 0.26

# Sunk cost in Germany (Vorabpauschale paid without recovery in IT)
paid_vorabpauschale = de_base_increase * de_tax

# Time axis (from 0 to 15 years)
t = np.arange(0, years + 1)

# --- SCENARIO 1: Liquidation in Germany prior to move to Italy ---
# Taxes are paid in DE prior to moving to Italy using the increase in the taxable base (leveraging Vorabpauschale)
de_taxable_base = transfer_value - (historical_price + de_base_increase)
de_tax_paid = de_taxable_base * de_tax
reinvested_capital = transfer_value - de_tax_paid

# Evolution in IT
gross_scen1 = reinvested_capital * (1 + annual_growth)**t
capital_gain_it_scen1 = gross_scen1 - reinvested_capital
# Net value if liquidated at year t
net_scen1 = gross_scen1 - (capital_gain_it_scen1 * it_tax)

# --- SCENARIO 2: Direct Transfer (No liquidation) when moving to Italy - no capital gain triggered in IT at transfer ---
# The transferred gross capital is higher, but carries a low cost basis (100), also Vorabpauschale paid in DE
# prior to moving to Italy is lost
gross_scen2 = transfer_value * (1 + annual_growth)**t
capital_gain_it_scen2 = gross_scen2 - historical_price
# Net value if liquidated at year t (also subtracting the sunk Vorabpauschale)
net_scen2 = gross_scen2 - (capital_gain_it_scen2 * it_tax) - paid_vorabpauschale

# --- SCENARIO 3: No transfer of assets prior to move to Italy, Vorabpauschale paid in DE throughout the whole investment
# and advance on taxable base accounted for when paying capital gains in IT, since base relies on broker base calculation in DE ---
# The gross capital is higher because retained in Germany, the taxable base is higher and continues to account for the fictional
# capital growth (135 + ...)
gross_scen3 = transfer_value * (1 + annual_growth * (1 - fictional_cap_growth * de_tax))**t
capital_gain_it_scen3 = gross_scen3 - ((historical_price + de_base_increase) * (1 + fictional_cap_growth)**t)
# Net value if liquidated at year t (also subtracting the sunk Vorabpauschale)
net_scen3 = gross_scen3 - (capital_gain_it_scen3 * it_tax)

# --- Generating the Plot ---
plt.figure(figsize=(10, 6))

plt.plot(t, net_scen1, label='Scenario 1: Liquidation in Germany', color='#1f77b4', linewidth=2.5)
plt.plot(t, net_scen2, label='Scenario 2: Transfer to Italy', color='#ff7f0e', linewidth=2.5)
plt.plot(t, net_scen3, label='Scenario 3: No liquidation, no transfer', color='#2ca02c', linewidth=2.5)

# Highlighting the crossover / break-even point
plt.fill_between(t, net_scen1, net_scen2, where=(net_scen1 > net_scen2), 
                 interpolate=True, color='#1f77b4', alpha=0.1)
plt.fill_between(t, net_scen1, net_scen2, where=(net_scen2 > net_scen1), 
                 interpolate=True, color='#ff7f0e', alpha=0.1)
plt.fill_between(t, net_scen2, net_scen3, where=(net_scen3 > net_scen2),
                 interpolate=True, color='#1f77b4', alpha=0.1)

plt.title('Realizable Net Value: Liquidation vs Transfer vs No Transfer', fontsize=14, fontweight='bold')
plt.xlabel('Years since change of tax residency', fontsize=12)
plt.ylabel('Net Capital in Pocket (€)', fontsize=12)
plt.xticks(np.arange(0, years + 1, 1))
plt.legend(fontsize=11)
plt.grid(True, linestyle='--', alpha=0.6)

plt.tight_layout()
plt.show()

# Printing the results at year 20
print(f"Historical Price: {historical_price:.2f} €")
print(f"Fictional Cap Growth: {fictional_cap_growth:.2f}")
print(f"Growth Spread: {growth_spread:.2f}")
print(f"Years Before Transfer: {years_before_transfer}")
print(f"De Base Increase: {de_base_increase:.2f} €")
print(f"Transfer Value: {transfer_value:.2f} €")
print(f"Annual Growth: {annual_growth:.2f}")
print(f"Years: {years}")
print(f"Net Scenario 1 (Year {years}): {net_scen1[-1]:.2f} €")
print(f"Net Scenario 2 (Year {years}): {net_scen2[-1]:.2f} €")
print(f"Net Scenario 3 (Year {years}): {net_scen3[-1]:.2f} €")
print(f"Net Difference Scenario 2 vs Scenario 1: {net_scen2[-1] - net_scen1[-1]:.2f} €")
print(f"Net Difference Scenario 3 vs Scenario 1: {net_scen3[-1] - net_scen1[-1]:.2f} €")
print(f"Percentage spread: {abs(net_scen1[-1] - net_scen2[-1]) / net_scen1[-1] * 100:.2f}%")
print(f"Percentage spread: {abs(net_scen1[-1] - net_scen3[-1]) / net_scen1[-1] * 100:.2f}%")