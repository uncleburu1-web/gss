from django.conf import settings
from django.db import models
from core.models import SyncModel


class Worker(SyncModel):
    """The shop-scoped staff profile. NOTE: this is a deliberate interim
    shape — `user` still points at Django's built-in auth.User (integer PK,
    no shop of its own). A proper multi-tenant `accounts` app with a custom,
    shop-aware User model is called out as step 2 in the architecture doc;
    swapping AUTH_USER_MODEL after migrations already exist is destructive,
    so it's done there deliberately rather than folded into this retrofit.
    """
    ROLE_CHOICES = [
        ('owner', 'Owner / Admin'),
        ('branch_manager', 'Branch manager'),
        ('seller', 'Seller (can log in and sell)'),
        ('reception', 'Receptionist'),
        ('technician', 'Service technician'),
        ('attendant', 'Shop attendant'),
        ('other', 'Other'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='worker',
        help_text='Linked login account — only set for workers who can access the system.',
    )
    full_name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='attendant')
    phone = models.CharField(max_length=30, blank=True)
    salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    hire_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    # created_at / updated_at / is_deleted / shop / id (UUID) come from SyncModel.

    class Meta:
        ordering = ['full_name']

    def __str__(self):
        return f'{self.full_name} ({self.get_role_display()})'

    @property
    def can_login(self):
        return self.user_id is not None


class AttendanceRecord(SyncModel):
    """One worker's attendance for one calendar day at one branch.

    Almost always created/edited by whoever has the 'mark_attendance'
    capability (reception, by default — see core.capabilities), but an
    owner/branch manager can always mark or correct one too, same
    unconditional-access rule as everywhere else in this app. `marked_by`
    records who actually entered it, separately from `worker` (who it's
    ABOUT), so a correction made by the owner days later is still
    distinguishable from reception's original same-day entry.

    One row per (worker, date) -- see the unique constraint below -- so
    "mark" is naturally an upsert: recording a second status for someone
    already marked today updates that same row rather than creating a
    duplicate.
    """
    STATUS_CHOICES = [
        ('present', 'Present'),
        ('late', 'Late'),
        ('absent', 'Absent'),
        ('half_day', 'Half day'),
        ('on_leave', 'On leave'),
    ]

    worker = models.ForeignKey(Worker, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='present')
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    marked_by = models.ForeignKey(
        Worker, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance_marked',
        help_text='Who recorded this entry — usually reception, sometimes an owner/manager correcting one.',
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-date', 'worker__full_name']
        constraints = [
            models.UniqueConstraint(fields=['worker', 'date'], name='unique_attendance_per_worker_per_day'),
        ]

    def __str__(self):
        return f'{self.worker.full_name} \u2014 {self.date} ({self.get_status_display()})'
