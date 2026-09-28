from django.utils import timezone
from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from core.permissions import IsOwner, HasCapability
from core.capabilities import has_capability
from core.mixins import ShopScopedMixin
from realtime.events import broadcast
from .models import Worker, AttendanceRecord
from .serializers import WorkerSerializer, AttendanceRecordSerializer


class WorkerViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    queryset = Worker.objects.filter(is_deleted=False)
    serializer_class = WorkerSerializer
    permission_classes = [IsOwner]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['role', 'is_active']
    search_fields = ['full_name', 'phone']


class AttendanceRecordViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """Attendance for this branch's workers.

    Reading is open to anyone signed in, but see get_queryset — that's
    narrower than it sounds for most roles. Creating, editing, or
    deleting an entry needs the 'mark_attendance' capability (reception,
    by default; always true for an owner/branch manager/CEO) — see
    core.capabilities for how an owner/CEO configures that from the
    Control Center.
    """
    queryset = AttendanceRecord.objects.filter(is_deleted=False).select_related('worker', 'marked_by')
    serializer_class = AttendanceRecordSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['worker', 'status', 'date']
    ordering_fields = ['date']

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy', 'mark'):
            return [HasCapability('mark_attendance')()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        if has_capability(self.request.user, 'view_attendance'):
            return qs
        # No blanket view access for this role -- everyone still gets to
        # see their OWN attendance history, the same self-service
        # transparency a worker already has over their own payslip-
        # adjacent data, just not anyone else's.
        worker = getattr(self.request.user, 'worker', None)
        return qs.filter(worker=worker) if worker else qs.none()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['shop'] = self.get_current_shop()
        return context

    def perform_create(self, serializer):
        marker = getattr(self.request.user, 'worker', None)
        instance = serializer.save(shop=self.get_current_shop(), marked_by=marker)
        broadcast(instance.shop, 'attendancerecord.created', {'id': str(instance.pk)})

    def perform_update(self, serializer):
        marker = getattr(self.request.user, 'worker', None)
        instance = serializer.save(marked_by=marker)
        broadcast(instance.shop, 'attendancerecord.updated', {'id': str(instance.pk)})

    @action(detail=False, methods=['get'])
    def today(self, request):
        """Every active worker at this branch, paired with today's (or
        `?date=`'s) attendance record if one exists yet — the data behind
        the Attendance page's daily roster, so the frontend never has to
        separately fetch /workers/ and /attendance/ and join them itself.
        Same read gate as the rest of this viewset: needs 'view_attendance'
        OR 'mark_attendance' (marking today's roster obviously requires
        being able to see it first).
        """
        if not (has_capability(request.user, 'view_attendance') or has_capability(request.user, 'mark_attendance')):
            raise PermissionDenied("Your role can't view the attendance roster.")

        shop = self.get_current_shop()
        target_date = request.query_params.get('date') or timezone.localdate().isoformat()
        records = {
            r.worker_id: r for r in
            AttendanceRecord.objects.filter(shop=shop, date=target_date, is_deleted=False).select_related('marked_by')
        }
        rows = []
        for worker in Worker.objects.filter(shop=shop, is_deleted=False, is_active=True).order_by('full_name'):
            record = records.get(worker.id)
            rows.append({
                'worker_id': str(worker.id),
                'worker_name': worker.full_name,
                'worker_role': worker.role,
                'attendance_id': str(record.id) if record else None,
                'status': record.status if record else None,
                'check_in_time': record.check_in_time if record else None,
                'check_out_time': record.check_out_time if record else None,
                'marked_by_name': record.marked_by.full_name if record and record.marked_by else None,
                'notes': record.notes if record else '',
            })
        return Response({'date': target_date, 'rows': rows})

    @action(detail=False, methods=['post'])
    def mark(self, request):
        """Upsert one worker's attendance for one day.

        POST {worker, date?, status, check_in_time?, check_out_time?,
        notes?} — always succeeds, updating the existing row if there is
        one, rather than a plain create() 400ing on the
        unique_together(worker, date) constraint the second time anyone
        touches the same person's entry the same day (e.g. reception
        fixing a mis-tap from "absent" to "late").
        """
        shop = self.get_current_shop()
        worker_id = request.data.get('worker')
        if not worker_id:
            return Response({'worker': 'This field is required.'}, status=400)
        worker = Worker.objects.filter(id=worker_id, shop=shop, is_deleted=False).first()
        if worker is None:
            return Response({'worker': 'That worker is not part of this branch.'}, status=400)

        target_date = request.data.get('date') or timezone.localdate().isoformat()
        marker = getattr(request.user, 'worker', None)
        defaults = {
            'status': request.data.get('status') or 'present',
            'check_in_time': request.data.get('check_in_time') or None,
            'check_out_time': request.data.get('check_out_time') or None,
            'notes': request.data.get('notes') or '',
            'marked_by': marker,
        }
        record, _ = AttendanceRecord.objects.update_or_create(
            worker=worker, date=target_date, defaults={**defaults, 'shop': shop},
        )
        broadcast(shop, 'attendancerecord.updated', {'id': str(record.id)})
        return Response(AttendanceRecordSerializer(record).data)
