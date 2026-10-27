"""
Data model of the Plantation Management System.

lookups    : every drop-down list (Gender, AttendanceStatus, OperationType, ...)
masters    : JobType (+ WageRate history), Product, Block, AgriInput, Employee
workforce  : Attendance, WorkEntry, GroupWorkEntry, SalaryPayment
operations : FieldOperation, Harvest, ProcessingBatch, Expense, Sale, SavedReport
"""

from .base import ZERO, LocalizedNameMixin, Lookup, TimeStampedModel, q2  # noqa: F401
from .lookups import (  # noqa: F401
    ApplicationMethod,
    AttendanceStatus,
    CropRole,
    EmploymentType,
    ExpenseCategory,
    Gender,
    InputType,
    JobCategory,
    OperationType,
    PaymentMode,
    PaymentStatus,
    ProcessType,
    ReceiptStatus,
    Unit,
)
from .masters import (  # noqa: F401
    DOSE_BASES,
    LINE_BASES,
    WAGE_WORKERS,
    AgriInput,
    Block,
    Employee,
    GangLeader,
    JobType,
    Product,
    WageBook,
    WageRate,
    WorkContext,
    close_open_rates,
    pick_rate,
    reapply_rates,
    wage_rate_on,
)
from .operations import (  # noqa: F401
    AgriInputPurchase,
    Expense,
    FieldOperation,
    FieldOperationInput,
    Harvest,
    ProcessingBatch,
    Sale,
    SaleReceipt,
    SavedReport,
)
from .workforce import (  # noqa: F401
    ENTRY_STATES,
    PAY_STATES,
    Attendance,
    GroupWorkEntry,
    SalaryPaid,
    SalaryPayment,
    WorkEntry,
)

LOOKUP_MODELS = [
    AttendanceStatus,
    OperationType,
    ApplicationMethod,
    InputType,
    Unit,
    JobCategory,
    EmploymentType,
    Gender,
    CropRole,
    ProcessType,
    ExpenseCategory,
    PaymentMode,
    PaymentStatus,
    ReceiptStatus,
]
