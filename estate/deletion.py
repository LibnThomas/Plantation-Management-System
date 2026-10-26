"""
What deleting a record does to the records linked to it - shown in the delete pop-up before anything
is deleted, then carried out in one go.

    delete  the linked record is deleted too (e.g. a payroll's payments made, a sale's payments received)
    clear   the link is only emptied, the record stays (e.g. "Block" on a harvest when the block goes)
    block   the record can't be deleted: it is a shared list / master entry still in use (a job type,
            an employee, a drop-down value ...). Those are never deleted together with business data -
            mark them inactive instead.
"""

from collections import OrderedDict

from django.db import models, transaction

from core.i18n import model_label

from . import models as m

# Shared lists and master records: deleting one must never take business records with it.
MASTERS = (m.Lookup, m.JobType, m.Block, m.Employee, m.Product, m.AgriInput, m.GangLeader)
MAX_DEPTH = 5
SAMPLE = 5


def is_master(model):
    return issubclass(model, MASTERS)


class Plan:
    def __init__(self, obj):
        self.obj = obj
        self.using = obj._state.db or "default"
        self.groups = OrderedDict()  # (model, action) -> {"count": n, "items": [...]}
        self.forced = []  # protected records deleted first (children before parents)
        self.blockers = []
        self._seen = {(type(obj), obj.pk)}
        self._walk(obj, 0)

    @property
    def can_delete(self):
        return not self.blockers

    def _add(self, model, action, count, items=()):
        group = self.groups.setdefault((model, action), {"count": 0, "items": []})
        group["count"] += count
        for item in items:
            if len(group["items"]) < SAMPLE:
                group["items"].append(str(item))

    def _walk(self, obj, depth):
        for rel in obj._meta.related_objects:
            if rel.many_to_many or not rel.field.concrete:
                continue
            on_delete = rel.on_delete
            if on_delete is models.DO_NOTHING:
                continue
            qs = rel.related_model._base_manager.using(self.using).filter(**{rel.field.name: obj})
            if on_delete in (models.CASCADE, models.PROTECT, models.RESTRICT):
                children = [c for c in qs if (type(c), c.pk) not in self._seen]
                if not children:
                    continue
                protected = on_delete is not models.CASCADE
                if protected and is_master(type(obj)):
                    self.blockers.extend(children)
                    self._add(rel.related_model, "block", len(children), children)
                    continue
                self._add(rel.related_model, "delete", len(children), children)
                for child in children:
                    self._seen.add((type(child), child.pk))
                    if depth < MAX_DEPTH:
                        self._walk(child, depth + 1)
                    if protected:
                        self.forced.append(child)  # after its own children: deepest first
            else:  # SET_NULL / SET_DEFAULT / SET(...)
                count = qs.count()
                if count:
                    self._add(rel.related_model, "clear", count, qs[:SAMPLE])

    def as_json(self):
        order = {"block": 0, "delete": 1, "clear": 2}
        groups = sorted(self.groups.items(), key=lambda kv: order[kv[0][1]])
        return {
            "can_delete": self.can_delete,
            "groups": [
                {
                    "label": model_label(model, plural=True),
                    "action": action,
                    "count": g["count"],
                    "items": g["items"],
                    "more": max(g["count"] - len(g["items"]), 0),
                }
                for (model, action), g in groups
            ],
        }

    def execute(self):
        """Delete the record and everything listed (in one transaction: all or nothing)."""
        if not self.can_delete:
            raise models.ProtectedError("in use", self.blockers)
        with transaction.atomic(using=self.using):
            for record in self.forced:
                # each one through its own delete(): balances, harvest figures etc. are kept right
                if type(record)._base_manager.using(self.using).filter(pk=record.pk).exists():
                    record.delete()
            self.obj.delete()
