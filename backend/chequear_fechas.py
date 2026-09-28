import pandas as pd

sismos = pd.read_csv("../data/sismos/sismos_csn.csv")
print("Fecha más antigua:", sismos["Fecha (UTC)"].min())
print("Fecha más reciente:", sismos["Fecha (UTC)"].max())
print("\nSismos M>=8.5:")
print(sismos[sismos["Magnitud [*]"] >= 8.5])