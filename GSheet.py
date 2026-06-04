import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file(
    "rapid-pottery-425007-q7-278c4933cff8.json",
    scopes=scope
)


client = gspread.authorize(creds)
# print(client.list_spreadsheet_files())
spreadsheet = client.open("KMs Reading Data")

sheet = spreadsheet.worksheet("Dump")

df = pd.DataFrame(sheet.get_all_records())

print(df.head())
