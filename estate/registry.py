"""
Central registry of every screen that lists / adds / edits records.

Each entry drives the generic list, form, CSV / PDF export, the menu, the admin and the
Report Builder data sources. Adding a module = a model + one ModuleConfig.
"""

from dataclasses import dataclass, field

from . import models as m


@dataclass
class ModuleConfig:
    slug: str
    model: type
    title: str
    icon: str
    group: str
    list_fields: list
    search_fields: list = field(default_factory=list)
    filter_fields: list = field(default_factory=list)  # FK / bool fields shown as drop-down filters
    date_field: str | None = "date"
    sum_fields: list = field(default_factory=list)  # numeric columns totalled in the list footer
    help_text: str = ""
    reportable: bool = True
    in_menu: bool = True
    # form field -> "wage" / "ot": filled from the work type's rate for the entry date (still editable)
    job_defaults: dict = field(default_factory=dict)
    # picking a record in <fk field> fills other fields: {"agri_input": {"unit_cost": "unit_cost"}}
    fk_defaults: dict = field(default_factory=dict)
    # (trigger fk, target fk, attribute): picking the trigger narrows the target list to rows with
    # the same attribute value, e.g. an operation shows only fertilizers of its usual type
    fk_narrow: tuple | None = None
    has_wage_history: bool = False
    # only staff may add / edit / delete (drop-down lists and wages); everyone can view and export
    staff_edit: bool = False
    # False: records are only created from elsewhere (salary paid: from the payroll's edit page)
    can_add: bool = True

    def can_edit(self, user):
        return user.is_staff or not self.staff_edit


def _lookup(model, slug, icon="bi-list-ul", extra=()):
    return ModuleConfig(
        slug=slug,
        model=model,
        title=str(model._meta.verbose_name_plural).capitalize(),
        icon=icon,
        group="Lists",
        list_fields=["name", *extra, "name_ml", "name_hi", "name_ta", "sort_order", "is_active"],
        search_fields=["name", "name_ml", "name_hi", "name_ta"],
        filter_fields=["is_active"],
        date_field=None,
        reportable=False,
        in_menu=False,
        staff_edit=True,
    )


MODULES = [
    ModuleConfig(
        slug="employees",
        model=m.Employee,
        title="Registration (Employees)",
        icon="bi-person-badge",
        group="Workforce",
        list_fields=[
            "employee_code",
            "full_name",
            "gender",
            "job_type",
            "employment_type",
            "phone",
            "date_of_joining",
            "is_active",
        ],
        search_fields=["full_name", "employee_code", "phone", "address"],
        filter_fields=["job_type", "employment_type", "gender", "is_active"],
        date_field="date_of_joining",
        help_text="Register every worker and staff member. Wages and OT rates come from their job type.",
    ),
    ModuleConfig(
        slug="attendance",
        model=m.Attendance,
        title="Attendance",
        icon="bi-calendar-check",
        group="Workforce",
        list_fields=["date", "employee", "status", "job_type", "block", "ot_hours", "remarks"],
        search_fields=["employee__full_name", "employee__employee_code", "remarks"],
        filter_fields=["status", "employee", "job_type", "block"],
        sum_fields=["ot_hours"],
        help_text="Tip: use 'Mark Daily Attendance' to mark everyone for a day in one screen.",
    ),
    ModuleConfig(
        slug="work-entries",
        model=m.WorkEntry,
        title="Work Details - Individual",
        icon="bi-person-workspace",
        group="Workforce",
        list_fields=[
            "date",
            "employee",
            "job_type",
            "block",
            "wage",
            "ot_hours",
            "ot_amount",
            "output_quantity",
            "total_amount",
            "remarks",
        ],
        search_fields=["employee__full_name", "remarks"],
        filter_fields=["job_type", "employee", "block"],
        sum_fields=["wage", "ot_hours", "ot_amount", "output_quantity", "total_amount"],
        job_defaults={"wage": "wage", "ot_rate": "ot"},
        help_text="Work done by one worker. Wage and OT rate come from the work type for that date; "
        "change them if this worker was paid differently - payroll then uses them.",
    ),
    ModuleConfig(
        slug="group-work",
        model=m.GroupWorkEntry,
        title="Daily Labour Count",
        icon="bi-people",
        group="Workforce",
        list_fields=[
            "date",
            "gang_leader",
            "job_type",
            "block",
            "workers_count",
            "wage_per_worker",
            "total_wages",
            "ot_morning_workers",
            "ot_morning_hours",
            "ot_evening_workers",
            "ot_evening_hours",
            "ot_rate_per_hour",
            "ot_amount",
            "total_cost",
            "remarks",
        ],
        search_fields=["remarks", "gang_leader__name"],
        filter_fields=["gang_leader", "job_type", "block"],
        sum_fields=[
            "workers_count",
            "total_wages",
            "ot_morning_workers",
            "ot_evening_workers",
            "ot_amount",
            "total_cost",
        ],
        job_defaults={"wage_per_worker": "wage", "ot_rate_per_hour": "ot"},
        help_text="Day labourers who are not registered, entered only as a count (e.g. 10 cardamom pickers "
        "today) with their wage and morning / evening overtime. Generate Payroll pays them per gang leader. "
        "Registered employees are not counted here: mark their attendance instead.",
    ),
    ModuleConfig(
        slug="wage-rates",
        model=m.WageRate,
        title="Salary Details (Wage Rates)",
        icon="bi-cash-stack",
        group="Salary",
        list_fields=[
            "job_type",
            "employment_type",
            "rate",
            "ot_rate_per_hour",
            "effective_from",
            "effective_to",
            "remarks",
        ],
        search_fields=["remarks", "job_type__name"],
        filter_fields=["job_type", "employment_type"],
        date_field="effective_from",
        help_text="Wage of each job type from a date to a date. Tip: edit them inside each Job Type.",
        staff_edit=True,
    ),
    ModuleConfig(
        slug="salary-payments",
        model=m.SalaryPayment,
        title="Salary Payments (Payroll)",
        icon="bi-wallet2",
        group="Salary",
        list_fields=[
            "employee",
            "gang_leader",
            "period_from",
            "period_to",
            "days_worked",
            "daily_rate",
            "basic_amount",
            "ot_amount",
            "bonus",
            "advance_deduction",
            "net_amount",
            "amount_paid",
            "balance",
            "pay_state",
            "status",
            "payment_date",
        ],
        search_fields=["employee__full_name", "gang_leader__name", "remarks"],
        filter_fields=["pay_state", "employee", "gang_leader", "status", "payment_mode"],
        date_field="period_to",
        sum_fields=[
            "days_worked",
            "basic_amount",
            "ot_amount",
            "bonus",
            "advance_deduction",
            "other_deduction",
            "net_amount",
            "amount_paid",
            "balance",
        ],
        help_text="Tip: use 'Generate Payroll' to calculate salaries automatically: employees from attendance, "
        "day labourers from the Daily Labour Count (one payment per gang leader). Edit a payroll and enter the "
        "current payment amount to record what you pay - all at once or in parts; an unpaid balance stays "
        "pending and can be paid later or added to the next payroll.",
    ),
    ModuleConfig(
        slug="salary-paid",
        model=m.SalaryPaid,
        title="Salary Paid (Payments made)",
        icon="bi-cash-coin",
        group="Salary",
        list_fields=["date", "salary", "amount", "status", "balance_after", "payment_mode", "remarks"],
        search_fields=["salary__employee__full_name", "salary__gang_leader__name", "remarks"],
        filter_fields=["status", "payment_mode"],
        sum_fields=["amount"],
        can_add=False,
        help_text="Every payment entered against a payroll, with the balance left after it. Only entries with "
        "the status 'Paid' count as paid; 'Pending' entries are recorded but not paid yet (edit one and set it "
        "to Paid once it is paid). To pay, open the payroll in Salary Payments (Edit) and enter the current "
        "payment amount.",
    ),
    ModuleConfig(
        slug="field-operations",
        model=m.FieldOperation,
        title="Fertilizer & Field Work",
        icon="bi-droplet-half",
        group="Field",
        list_fields=[
            "date",
            "operation_type",
            "block",
            "agri_input",
            "quantity",
            "unit_cost",
            "dosage",
            "purpose",
            "input_cost",
            "labour_cost",
            "total_cost",
            "next_due_date",
        ],
        search_fields=["purpose", "remarks", "dosage", "agri_input__name"],
        filter_fields=["operation_type", "block", "agri_input", "method"],
        sum_fields=["quantity", "workers_count", "input_cost", "labour_cost", "total_cost"],
        fk_defaults={"agri_input": {"unit_cost": "unit_cost"}},
        fk_narrow=("operation_type", "agri_input", "input_type_id"),
        help_text="When, where and what was applied (fertilizer, spray, weeding...), quantity and cost. "
        "Cost per unit comes from the Fertilizer / Chemical list.",
    ),
    ModuleConfig(
        slug="harvests",
        model=m.Harvest,
        title="Product Details (Harvest)",
        icon="bi-basket",
        group="Production",
        list_fields=[
            "date",
            "product",
            "job_type",
            "block",
            "quantity",
            "ind_people",
            "count_people",
            "ind_ot_hours",
            "count_ot_hours",
            "total_hours",
            "per_hour",
            "per_person_full_day",
            "per_person_ot",
            "per_person_full_day_ot",
            "per_person_average",
            "harvest_round",
            "quality_notes",
        ],
        search_fields=["harvest_round", "remarks", "quality_notes"],
        filter_fields=["product", "job_type", "block"],
        sum_fields=["quantity"],
        help_text="Raw produce collected from the farm: cardamom, pepper, coffee... The people who did the "
        "picking job on the same date are fetched from Work Details - Individual and the Daily Labour Count, "
        "and the harvest per hour and per person is worked out in hours: 10 people x 8 h + 6 OT hours = 86 h; "
        "200 kg / 86 h = 2.33 kg an hour, 18.60 kg for one person's full day.",
    ),
    ModuleConfig(
        slug="processing",
        model=m.ProcessingBatch,
        title="Processed Product Details",
        icon="bi-fire",
        group="Production",
        list_fields=[
            "date",
            "batch_no",
            "product",
            "process_type",
            "input_quantity",
            "output_quantity",
            "conversion_ratio",
            "recovery_percent",
            "output_grade",
            "processing_cost",
            "total_cost",
            "remarks",
        ],
        search_fields=["batch_no", "output_grade", "remarks"],
        filter_fields=["product", "process_type"],
        sum_fields=["input_quantity", "output_quantity", "total_cost"],
        help_text="Raw produce in, processed output out (e.g. green to dry cardamom). "
        "Ratio, recovery % and the batch's total processing cost (input kg x cost per kg) are calculated.",
    ),
    ModuleConfig(
        slug="sales",
        model=m.Sale,
        title="Sales",
        icon="bi-graph-up-arrow",
        group="Finance",
        list_fields=[
            "date",
            "product",
            "quantity",
            "rate",
            "total_amount",
            "amount_received",
            "balance",
            "receipt_state",
            "last_received_on",
            "buyer",
            "remarks",
            "grade",
            "invoice_no",
        ],
        search_fields=["buyer", "invoice_no", "grade", "remarks"],
        filter_fields=["receipt_state", "product"],
        sum_fields=["quantity", "total_amount", "amount_received", "balance"],
        fk_defaults={"product": {"rate": "typical_rate"}},
        help_text="Produce sold: quantity, rate and buyer / auction centre. Enter the money as it comes in - "
        "all at once or in parts (e.g. 30 % first, 20 % later, the rest next day): each payment is kept with "
        "its received date, and the balance still to receive is shown.",
    ),
    ModuleConfig(
        slug="sale-receipts",
        model=m.SaleReceipt,
        title="Sale Payments Received",
        icon="bi-cash-stack",
        group="Finance",
        list_fields=["date", "sale", "amount", "balance_after", "payment_mode", "remarks"],
        search_fields=["sale__buyer", "sale__invoice_no", "remarks"],
        filter_fields=["payment_mode"],
        sum_fields=["amount"],
        can_add=False,
        help_text="Every payment received for a sale, with the balance left to receive after it. To enter a "
        "payment, open the sale (Edit) and enter the amount received now.",
    ),
    ModuleConfig(
        slug="expenses",
        model=m.Expense,
        title="Other Expenses",
        icon="bi-receipt",
        group="Finance",
        list_fields=["date", "category", "description", "block", "vendor", "amount", "bill_no"],
        search_fields=["description", "vendor", "bill_no", "remarks"],
        filter_fields=["category", "block"],
        sum_fields=["amount"],
        help_text="Every non-wage spend: fuel, electricity, equipment, transport, purchases...",
    ),
    ModuleConfig(
        slug="gang-leaders",
        model=m.GangLeader,
        title="Gang Leaders / Contractors",
        icon="bi-person-vcard",
        group="Masters",
        list_fields=["name", "phone", "address", "is_active"],
        search_fields=["name", "phone", "address", "notes"],
        filter_fields=["is_active"],
        date_field=None,
        reportable=True,
        help_text="People who bring day labourers and receive their pay. Choose them in the Daily Labour Count.",
    ),
    ModuleConfig(
        slug="job-types",
        model=m.JobType,
        title="Job Types",
        icon="bi-tools",
        group="Masters",
        list_fields=["name", "category", "default_daily_wage", "default_ot_rate", "is_active"],
        search_fields=["name", "description"],
        filter_fields=["category", "is_active"],
        date_field=None,
        has_wage_history=True,
        staff_edit=True,
        help_text="Each work type and its wage history: which daily wage and OT rate applied from which date "
        "to which date.",
    ),
    ModuleConfig(
        slug="blocks",
        model=m.Block,
        title="Blocks / Plots",
        icon="bi-grid-3x3-gap",
        group="Masters",
        list_fields=[
            "name",
            "area_acres",
            "main_crop",
            "variety",
            "plant_count",
            "year_planted",
            "irrigated",
            "is_active",
        ],
        search_fields=["name", "variety", "notes"],
        filter_fields=["main_crop", "irrigated", "is_active"],
        date_field=None,
        sum_fields=["area_acres", "plant_count"],
    ),
    ModuleConfig(
        slug="products",
        model=m.Product,
        title="Products / Crops",
        icon="bi-flower1",
        group="Masters",
        list_fields=[
            "name",
            "crop_role",
            "unit",
            "raw_form",
            "processed_form",
            "typical_recovery_percent",
            "typical_rate",
            "harvest_months",
            "is_active",
        ],
        search_fields=["name", "name_ml", "name_hi", "name_ta"],
        filter_fields=["crop_role", "is_active"],
        date_field=None,
    ),
    ModuleConfig(
        slug="agri-inputs",
        model=m.AgriInput,
        title="Fertilizers / Chemicals",
        icon="bi-eyedropper",
        group="Masters",
        list_fields=["name", "input_type", "brand", "composition", "unit", "unit_cost", "is_active"],
        search_fields=["name", "brand", "composition"],
        filter_fields=["input_type", "is_active"],
        date_field=None,
    ),
    # ---------------- drop-down lists (reached from Setup > Drop-down lists) ----------------
    _lookup(m.AttendanceStatus, "attendance-statuses", "bi-check2-square", extra=("pay_fraction", "is_leave", "color")),
    _lookup(m.OperationType, "operation-types", "bi-droplet", extra=("input_type",)),
    _lookup(m.ApplicationMethod, "application-methods"),
    _lookup(m.InputType, "input-types"),
    _lookup(m.Unit, "units", "bi-rulers"),
    _lookup(m.JobCategory, "job-categories"),
    _lookup(m.EmploymentType, "employment-types"),
    _lookup(m.Gender, "genders"),
    _lookup(m.CropRole, "crop-roles"),
    _lookup(m.ProcessType, "process-types"),
    _lookup(m.ExpenseCategory, "expense-categories"),
    _lookup(m.PaymentMode, "payment-modes"),
    _lookup(m.PaymentStatus, "payment-statuses", extra=("is_final",)),
    _lookup(m.ReceiptStatus, "receipt-statuses", extra=("means",)),
]

REGISTRY = {cfg.slug: cfg for cfg in MODULES}
BY_MODEL = {cfg.model: cfg for cfg in MODULES}  # the screen that adds records of a model ("Add new" pop-ups)
LOOKUP_MODULES = [cfg for cfg in MODULES if cfg.group == "Lists"]

MENU_GROUPS = [
    ("Workforce", "Workers & Work"),
    ("Salary", "Salary"),
    ("Field", "Fertilizer & Field"),
    ("Production", "Production"),
    ("Finance", "Sales & Expenses"),
    ("Masters", "Setup / Masters"),
]


def get(slug):
    return REGISTRY.get(slug)
