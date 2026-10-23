from django.urls import path

from . import views

urlpatterns = [
    path('pairs/', views.PairListView.as_view(), name='pair-list'),
    path('pairs/<str:symbol>/history/', views.PairHistoryView.as_view(), name='pair-history'),
    path('currencies/', views.CurrencyListView.as_view(), name='currency-list'),
    path('currencies/<str:code>/history/', views.CurrencyHistoryView.as_view(), name='currency-history'),
    path('status/', views.StatusView.as_view(), name='status'),
]
