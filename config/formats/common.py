"""Date formats for every language of the app: DD/MM/YY (see core/dates.py)."""

DATE_FORMAT = "d/m/y"
SHORT_DATE_FORMAT = "d/m/y"
DATETIME_FORMAT = "d/m/y H:i"
SHORT_DATETIME_FORMAT = "d/m/y H:i"
DATE_INPUT_FORMATS = ["%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%y", "%d-%m-%Y", "%d.%m.%y", "%d.%m.%Y"]
DATETIME_INPUT_FORMATS = [f"{d} %H:%M" for d in DATE_INPUT_FORMATS] + [f"{d} %H:%M:%S" for d in DATE_INPUT_FORMATS]
