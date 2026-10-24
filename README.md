# Plantation Management System (PMS)

*Owned and maintained by **Mentors Soft Tech**.*

A mobile-friendly web application (Python · Django · PostgreSQL · HTML · CSS) to record **everything that happens on a cardamom estate** (with pepper / coffee inter-crops) – workers, attendance, daily work, wages, fertilizer & spraying, harvest, processing, sales and expenses – and turn it into analysis: which work costs the most, where money goes, production per day / month / quarter / financial year / crop season, recovery after drying, yield and cost per block, when each block was last manured or sprayed, and more.

* **4 languages** – English (default), മലയാളം, हिन्दी, தமிழ். Every user picks their own language (top bar or login page).
* **Rename anything** – every field name, button, menu item and message can be renamed / re-translated by the customer (*Labels & translations*).
* **No hard-coded data** – every drop-down (attendance statuses, operations, units, payment modes, crop roles …) is a table with IDs and foreign keys. The starting values are inserted **once, by a migration** (`estate/migrations/0002_initial_data.py`); after that they are ordinary data you can rename, translate, re-order, add or switch off (*Drop-down lists*).
* **PDF download** of every list, the Analysis page, Report Builder results and payslips (Malayalam / Hindi / Tamil included).
* **App Settings** (administrators only) – estate profile and logo, colour theme / font / text size, subscription (licence key), automatic logout, backup download and an activity log.
* **Demo mode** – fills a separate demo database with 18 months of realistic sample data to showcase the app; switch back to your own data at any time. Your real data is never touched.

---

## Modules

| # | Module (menu name) | What you record | Calculated automatically |
|---|---|---|---|
| 1 | **Registration (Employees)** | Name, gender, phone, address, job type, employment type, joining & leaving date | Employee code (EMP0001…). Wage and OT rate come from the job type (its wage history for the date) |
| 2 | **Attendance** + **Mark Daily Attendance** | Employee, date, attendance status, OT hours, work done, block, remarks | One-screen marking for all workers per day. Each status has its own *pay fraction* (Full day = 1, Half day = 0.5 …) |
| 3a | **Work Details – Individual** | Date, employee, work type, block, wage, OT hours, OT rate, output, comments | Wage / OT rate filled from the work type for that date **and employee type** (editable); OT amount, total |
| 3b | **Daily Labour Count** | Day labourers who are **not registered** (e.g. today 10 cardamom pickers), entered only as a count: date, **gang leader / paid to**, work type, block, male / female workers, wage per worker, OT workers & hours morning / evening, OT rate, area covered | Total workers, total wages, OT amount, total cost. Generate Payroll pays them per gang leader |
| ⚙ | **Job Types → Wage history** | Daily wage + OT rate valid **from date – to date**, for all employee types or one type | Every entry takes the rate in force **on its own date**. A rate without "Date to" closes automatically when a newer one is added and re-opens if that newer one is deleted; overlaps are rejected |
| 4 | **Salary Details (Wage Rates)** + **Salary Payments (Payroll)** + **Generate Payroll** | Period, bonus, advance & other deductions, payment mode, status | **Registered employees:** each working day is paid at **1.** the employee's *Individual work details* for that day, else **2.** the *Job type* wage for that date and employee type. **Day labourers (Daily Labour Count):** one payment per **gang leader** for the period – man-days × wage per worker plus their overtime, exactly as entered each day; these payments show *Paid to: <gang leader>* and lines marked *Daily labour count · <work type>*. Registered employees are never part of a count, so nobody is paid twice. Re-generating keeps bonus, deductions and payment details you typed |
| 6 | **Fertilizer & Field Work** | Date, operation, block, fertilizer / chemical (list narrowed to the operation's input type), quantity, cost per unit (**filled from the Fertilizer / Chemical list, editable**), dosage, method, target pest, workers, wage, area, weather, **next due date** | Input cost, labour cost, total cost (live while typing); dashboard reminder for due rounds |
| 7 | **Product Details (Harvest)** | Date, product, block, harvest round, quantity, quality | – |
| 8 | **Processed Product Details** | Date, product, process, raw quantity, processed quantity, grade, processing cost | Ratio, recovery %, batch number |
| + | **Sales** / **Other Expenses** | Product, quantity, rate (from the product's usual rate), buyer, payment status / category, amount, bill no. | Total amount |
| 5 | **Reports & Analysis** | see below | |
| ⚙ | **Masters** | Job types, Blocks / plots, Products / crops, Fertilizers / chemicals, **Gang leaders / contractors** | |
| ⚙ | **Drop-down lists** | 14 lists: genders, employment types, job categories, crop roles, units, input types, attendance statuses, payment modes / statuses, operations, application methods, process types, expense categories, receipt statuses – each with Malayalam / Hindi / Tamil names | |

Every drop-down in a form ends with **＋ Add new…**: it opens that list's form in a pop-up (e.g. a new fertilizer, gang leader, job type, block or drop-down value) and, once saved, the new item is added and selected – nothing typed on the page is lost, and its cost / wage fill in as usual. (Lists that only staff may change show this entry only to staff.)

Every list screen has search, date range, drop-down filters, totals, pagination, **Excel (CSV)** and **PDF** export, edit and delete.
Every table (lists, Analysis, Report Builder, dashboard, payroll preview …) also has an **in-browser search**: a box that
filters the rows already on the screen across all columns, optional **per-column filters** (text, or `>100`, `<50`, `=0`
for numbers), **click-to-sort** headings and a **"Shown rows" total** of the visible rows – all without asking the server.
Lists let you load 25 – 500 rows per page for it; the server search / filters above still cover all records. Drop-down lists, job types and wage rates can be changed only by *staff* users; everyone can view and export them.

## Reports & Analysis

1. **Dashboard** – workers present today, cost this month, harvest this season, sales vs cost this FY, charts, field work due soon.
2. **Analysis** – any date range, grouped by day, week, month, quarter, calendar year, **financial year** or **crop season**: harvest and processed output per product, processing recovery, total cost / revenue / profit, cost per unit of the main product, cost breakdown, **labour cost by work type**, fertilizer / chemical usage, **last date of every operation per block**, block yield and cost per acre, man-days and attendance summary. Download as **PDF**.
3. **Report Builder** – any module, any column (also of related records), filters on any field, group by any field / period, Sum / Average / Min / Max / Count, cross-tab, chart, CSV / PDF export, saved reports and 16 ready-made reports.

---

## Installation

### 1. Requirements
- Python 3.11+
- PostgreSQL 13+

### 2. Create the database
```sql
-- in psql
CREATE USER pms_user WITH PASSWORD 'strong-password' CREATEDB;   -- CREATEDB: needed for demo mode
CREATE DATABASE plantation_db OWNER pms_user;
```
Demo mode creates a second database `plantation_db_demo` automatically the first time it is switched on
(that is why the user needs `CREATEDB`). You can also create it yourself and set `DEMO_DB_NAME`.

### 3. Install & run
```bash
cd plantation_ms
pip install pipenv                   # once
pipenv sync                          # install dependencies from Pipfile.lock
pipenv shell                         # activate environment
# (or: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt)

cp .env.example .env                 # then edit DB_USER / DB_PASSWORD / ESTATE_NAME ...
python manage.py migrate             # creates tables + the starting drop-down values + labels
python manage.py createsuperuser     # your login
python manage.py runserver 0.0.0.0:8000
```
Open http://localhost:8000 – on your phone use `http://<your-computer-IP>:8000` on the same Wi-Fi
(add the IP to `DJANGO_ALLOWED_HOSTS`). No internet needed at the estate: Bootstrap, icons, Chart.js and
the fonts are bundled in `static/`.

> **Upgrading:** from the previous version (with languages / demo mode) just run `python manage.py migrate`.
> This version adds database indexes for faster lists, reports and payroll: `migrate` creates them (a few seconds, no data is changed). Installed phone/desktop apps pick up the new scripts automatically.
> **This update:** dates are DD/MM/YY everywhere (screens, date boxes with a calendar pop-up, PDF and CSV); Daily Labour Count takes half workers (13.5); processing cost is now per KG and each batch shows its total (input KG x cost per KG). Existing batches get their total calculated by `migrate`.
> **Harvest per person:** enter only the total quantity. For the harvest's picking job the app fetches, for the same date, the people and OT hours from Work Details - Individual (present employees; half day = 0.5; no attendance = full day) and from the Daily Labour Count, and works out in hours (App Settings → hours in a full day, default 8): harvest per hour, per person for a full day and for a full day + OT. `migrate` works this out for your existing harvests. While adding a harvest the same figures are shown live under the form as you type the quantity; the list and Report Builder (preset 'Harvest per person by day') also show the job type, 1 person's OT harvest and the average per person.
> From older versions (before languages) start with an empty database, because the migrations were rebuilt then.

### 4. Languages, renaming and translations
* Users switch language from the drop-down in the top bar (remembered per user).
* **Labels & translations** (staff only, user menu) lists every text in the app – field names, buttons,
  menu items, help texts, messages – with English, Malayalam, Hindi and Tamil columns. Edit any of them;
  the change shows immediately for everybody. *Reset* goes back to the original wording.
  Export to CSV, translate / review in Excel, and import it back.
* The starting Malayalam, Hindi and Tamil texts come from `core/translations.json`. They were machine
  produced – **please have a native speaker review them** (export → correct → import).
* Names of drop-down values (e.g. attendance statuses, operations) are translated in *Drop-down lists*.
* After adding new screens / fields: `python manage.py sync_labels` (also runs automatically after `migrate`).

### 5. Demo mode
User menu → **Demo mode** → *Switch to demo*. The first time, the demo database is created and filled with
18 months of sample data (24 workers, attendance, harvest, drying batches, fertilizer & spray rounds, sales,
expenses, payroll). A yellow banner shows while you are in demo mode; anything added there stays in the demo
data. *Back to my data* returns to your real data, which is never read or changed by demo mode.
Staff can *regenerate* fresh sample data. From the command line: `python manage.py demo_data --reset`.

### 6. App Settings (administrators only)
User menu → **App Settings** (only *superusers* see it):

| Tab | What you can do |
|---|---|
| **Estate profile** | Estate name, address, phone, email, **logo** (top bar, login page, PDFs), currency symbol, first month of the financial year / crop season, prefixes of automatic numbers (EMP0001, PB2610-0001) |
| **Theme** | Pick a ready-made theme (Estate green, Cardamom, Monsoon blue, Coffee brown, Pepper red, Slate) or your own main / top-bar / background colours, the font and the text size, with a live preview. Colours that would make text unreadable are refused. Each user can still choose a bigger **Text size** for themselves in the user menu |
| **Subscription** | **Valid till** date and users allowed – typed by the administrator, fetched from an **API URL** (switch on/off), or filled by a licence key; Installation ID, last API check |
| **Security & backup** | Automatic logout after N minutes of inactivity (for shared phones); **Download backup** of all real data as one JSON file (restore with `python manage.py loaddata <file>` on an empty database); link to user management |
| **Activity log** | Who added, changed or deleted which record, and when (filters by user / action / text) |

**Subscription – the "valid till" date.** The Subscription tab has a switch **Update the valid till date from the API**:

* **Off (manual):** the administrator types the *Valid till* date and *Users allowed* and saves (or pastes a licence key
  from the supplier, which fills both). Blank date = 30-day free trial from the first day of use.
* **On (API):** the date is fetched from the *API URL* when the app starts (first request after the server starts),
  again every *Check again every (hours)* (default 12), and when you press **Check now**. While the API is on, the date
  can't be typed. If the URL can't be reached, the last date received stays in force, so a network problem never
  locks anyone out. From cron: `python manage.py check_subscription`.

What the API is sent and must answer:
```
GET <API URL>?install_id=59C8-23DB-4903&estate=Green+Valley
Authorization: Bearer <API token>            (only if a token is set)

{"valid_until": "2027-03-31", "max_users": 10, "plan": "Standard"}
```
Only the date is required (YYYY-MM-DD or DD-MM-YYYY); the field name is configurable (`data.valid_until` for nested
JSON), and plain text `2027-03-31` also works. A static JSON file per customer on any web server is enough.

Seven days before the date everybody sees a reminder; after it there are 7 days of grace (red "payment pending"
banner, everything still works); after that the app is **read-only** – pages, reports, CSV and PDF still open, but
nothing can be saved – until the date is renewed. **No data is ever deleted.** If *Users allowed* is set, only that many
(non-administrator) users can log in.

**Vendor-controlled installations.** Set `SUBSCRIPTION_MANUAL_ALLOWED=False` and `SUBSCRIPTION_API_URL=…`
(+ `SUBSCRIPTION_API_TOKEN`) in `.env`: the API is then always on, its URL comes from the server settings, and the
customer's administrator can't type a date or switch the API off.

### 7. Server settings (`.env`)
| Setting | Default | Meaning |
|---|---|---|
| `TIME_ZONE` | Asia/Kolkata | |
| `ESTATE_NAME`, `CURRENCY_SYMBOL`, `FINANCIAL_YEAR_START_MONTH`, `CROP_SEASON_START_MONTH`, `EMPLOYEE_CODE_PREFIX`, `BATCH_NO_PREFIX` | My Cardamom Estate, ₹, 4, 8, EMP, PB | **starting values only** – copied into App Settings by the first `migrate`; change them in App Settings afterwards |
| `LICENCE_SUPPORT_CONTACT` | Mentors Soft Tech | renewal contact shown on the Subscription tab |
| `SUBSCRIPTION_MANUAL_ALLOWED` | True | False = only the API sets the valid till date |
| `SUBSCRIPTION_API_URL`, `SUBSCRIPTION_API_TOKEN` | – | default API URL / token (always used when manual is not allowed) |
| `DEMO_DB_NAME` | `<DB_NAME>_demo` | demo database |
| `DB_ENGINE=sqlite` (+ `SQLITE_PATH`, `SQLITE_DEMO_PATH`) | – | try the app without PostgreSQL |

### 8. Users
**Admin / Users** (user menu) creates logins. *Superusers* (administrators) can do everything including App Settings.
Users with *Staff status* can change drop-down lists, job types, wage rates and labels; others can enter daily data,
see reports and export.

### 9. Production deployment (summary)
```bash
# .env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<long random string>        # required when DEBUG is False
DJANGO_ALLOWED_HOSTS=pms.yourdomain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://pms.yourdomain.com

python manage.py collectstatic
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
```
Put Nginx (or Caddy) in front for HTTPS and to serve `/static/` from `staticfiles/`.
Back up regularly: `pg_dump plantation_db > backup_$(date +%F).sql`.

---

## Recommended daily routine
1. **Morning** – *Mark Daily Attendance* (one screen, all workers).
2. **Evening** – *Daily Labour Count* for each gang of day labourers (with its gang leader), and *Individual* work for registered staff paid differently.
3. Picking days – *Product Details (Harvest)*; after drying *Processed Product Details*.
4. Fertilizer / spray rounds – *Fertilizer & Field Work* with **next due date** (shows on the dashboard).
5. Month end – *Generate Payroll*, add advances / bonus, mark paid, print / download payslips.

> **Avoid counting labour twice.** Labour cost in the analysis = Work Details (individual) + Daily Labour Count
> + any wage entered in Fertilizer & Field Work. If a gang is already recorded in *Work Details*, leave the
> wage in the field-work entry at 0. Payroll is the cash-paid view of the same labour and is not added again.

## Project structure
```
config/        settings (.env), urls
core/          App Settings (models, forms, theme in appsettings.py, subscription in licence.py, activity log in
               audit.py, user-limit login in auth.py), languages & labels (i18n.py, Label table, translations.json), demo mode (demo.py, dbrouter.py),
               PDF engine (pdf.py), CSV export helper, middleware
estate/        models (lookups, masters, workforce, operations), generic CRUD, attendance, payroll,
               demo data generator, migrations (0002 = starting drop-down values), tests
  registry.py  one entry per module → menu, list columns, filters, totals, exports, report datasets
reports/       dashboard, analysis (analytics.py) and report builder (builder.py)
templates/     Bootstrap 5 responsive templates;  static/  css, js, vendor libraries, fonts
```
Adding a module = a model + one `ModuleConfig` in `estate/registry.py`, then `makemigrations` / `migrate`.
It gets list / add / edit / delete / CSV / PDF screens, a menu item, translatable labels and a Report Builder data source.

Run tests: `python manage.py test` · lint / format: `ruff check . && ruff format .`
