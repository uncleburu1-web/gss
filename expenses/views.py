from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from core.permissions import HasCapability
from core.mixins import ShopScopedMixin
from core.cloudinary_utils import InvalidImage, delete_image, upload_image, validate_image
from realtime.events import broadcast
from .models import DEFAULT_EXPENSE_CATEGORIES, Expense, ExpenseCategory
from .serializers import ExpenseCategorySerializer, ExpenseSerializer


class ExpenseCategoryViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    queryset = ExpenseCategory.objects.filter(is_deleted=False)
    serializer_class = ExpenseCategorySerializer
    permission_classes = [HasCapability('manage_expenses')]

    def get_queryset(self):
        qs = super().get_queryset()
        # Lazy default-seeding: a brand-new shop (or one that's deleted
        # every category it had) gets the starter set on its very next
        # fetch, rather than a data migration touching every shop that
        # ever existed. After this runs once, these are perfectly
        # ordinary rows — rename or delete them like any custom category.
        shop = self.get_current_shop()
        if shop is not None and not qs.filter(shop=shop).exists():
            ExpenseCategory.objects.bulk_create([
                ExpenseCategory(shop=shop, name=name, is_default=True) for name in DEFAULT_EXPENSE_CATEGORIES
            ])
            qs = super().get_queryset()
        return qs


class ExpenseViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    queryset = Expense.objects.filter(is_deleted=False).select_related('category', 'recorded_by')
    serializer_class = ExpenseSerializer
    permission_classes = [HasCapability('manage_expenses')]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'payment_method', 'date']
    search_fields = ['description', 'notes', 'category__name']
    ordering_fields = ['date', 'amount', 'created_at']
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['shop'] = self.get_current_shop()
        return context

    def perform_create(self, serializer):
        marker = getattr(self.request.user, 'worker', None)
        instance = serializer.save(shop=self.get_current_shop(), recorded_by=marker)
        broadcast(instance.shop, 'expense.created', {'id': str(instance.pk)})

    @action(detail=True, methods=['post', 'delete'], url_path='receipt')
    def receipt(self, request, pk=None):
        """POST (multipart/form-data, file field named `receipt`) attaches
        a photo of the receipt/document to this expense, replacing any
        existing one in place. DELETE removes it. Same pattern as
        InventoryItemViewSet.image — its own endpoint because Cloudinary
        needs real file bytes, which the plain JSON expense body was never
        designed to carry.
        """
        expense = self.get_object()

        if request.method == 'DELETE':
            delete_image(expense.receipt_public_id)
            expense.receipt_url = None
            expense.receipt_public_id = ''
            expense.save(update_fields=['receipt_url', 'receipt_public_id', 'updated_at'])
            broadcast(expense.shop, 'expense.updated', {'id': str(expense.pk)})
            return Response(ExpenseSerializer(expense).data)

        file_obj = request.data.get('receipt')
        try:
            validate_image(file_obj)
        except InvalidImage as exc:
            return Response({'detail': str(exc)}, status=400)

        try:
            url, public_id = upload_image(
                file_obj, folder='expense_receipts', previous_public_id=expense.receipt_public_id or None,
            )
        except Exception:
            return Response({'detail': 'Could not upload this receipt right now — please try again.'}, status=502)

        expense.receipt_url = url
        expense.receipt_public_id = public_id
        expense.save(update_fields=['receipt_url', 'receipt_public_id', 'updated_at'])
        broadcast(expense.shop, 'expense.updated', {'id': str(expense.pk)})
        return Response(ExpenseSerializer(expense).data)
