import matplotlib.pyplot as plt
import numpy as np

# Portfolio parameters
historical_price = 100.0
transfer_value = 150.0
de_base_increase = 10.0
annual_growth = 0.06
years = 15
de_tax = 0.26375
it_tax = 0.26

# Sunk cost in Germany (Vorabpauschale paid without recovery in IT)
paid_vorabpauschale = de_base_increase * de_tax

# Time axis (from 0 to 15 years)
t = np.arange(0, years + 1)

# --- SCENARIO 1: Liquidation in Germany ---
# Taxes are paid in DE using the increase in the taxable base
de_taxable_base = transfer_value - (historical_price + de_base_increase)
de_tax_paid = de_taxable_base * de_tax
reinvested_capital = transfer_value - de_tax_paid

# Evolution in IT
gross_scen1 = reinvested_capital * (1 + annual_growth)**t
capital_gain_it_scen1 = gross_scen1 - reinvested_capital
# Net value if liquidated at year t
net_scen1 = gross_scen1 - (capital_gain_it_scen1 * it_tax)

# --- SCENARIO 2: Direct Transfer (No liquidation) ---
# The transferred gross capital is higher (200), but carries a low cost basis (100)
gross_scen2 = transfer_value * (1 + annual_growth)**t
capital_gain_it_scen2 = gross_scen2 - historical_price
# Net value if liquidated at year t (also subtracting the sunk Vorabpauschale)
net_scen2 = gross_scen2 - (capital_gain_it_scen2 * it_tax) - paid_vorabpauschale

# --- Generating the Plot ---
plt.figure(figsize=(10, 6))

plt.plot(t, net_scen1, label='Scenario 1: Liquidation in Germany', color='#1f77b4', linewidth=2.5)
plt.plot(t, net_scen2, label='Scenario 2: Transfer to Italy', color='#ff7f0e', linewidth=2.5)

# Highlighting the crossover / break-even point
plt.fill_between(t, net_scen1, net_scen2, where=(net_scen1 > net_scen2), 
                 interpolate=True, color='#1f77b4', alpha=0.1)
plt.fill_between(t, net_scen1, net_scen2, where=(net_scen2 > net_scen1), 
                 interpolate=True, color='#ff7f0e', alpha=0.1)

plt.title('Realizable Net Value: Liquidation vs Transfer', fontsize=14, fontweight='bold')
plt.xlabel('Years since change of tax residency', fontsize=12)
plt.ylabel('Net Capital in Pocket (€)', fontsize=12)
plt.xticks(np.arange(0, years + 1, 1))
plt.legend(fontsize=11)
plt.grid(True, linestyle='--', alpha=0.6)

plt.tight_layout()
plt.show()

# Printing the results at year 15
print(f"Net Scenario 1 (Year 15): {net_scen1[-1]:.2f} €")
print(f"Net Scenario 2 (Year 15): {net_scen2[-1]:.2f} €")
print(f"Difference: {abs(net_scen1[-1] - net_scen2[-1]):.2f} €")