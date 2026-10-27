<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div class="card budget-card">
      <div class="budget-header">
        <label for="budget-slider" class="budget-label">{{ t('restocking.budget') }}</label>
        <span class="budget-value">{{ formatCurrency(budget, currentCurrency) }}</span>
      </div>
      <input
        id="budget-slider"
        v-model.number="budget"
        type="range"
        class="budget-slider"
        :min="BUDGET_MIN"
        :max="BUDGET_MAX"
        :step="BUDGET_STEP"
        :disabled="submitting"
      />
      <div class="budget-scale">
        <span>{{ formatCurrency(BUDGET_MIN, currentCurrency) }}</span>
        <span>{{ formatCurrency(BUDGET_MAX, currentCurrency) }}</span>
      </div>
      <p class="budget-hint">{{ t('restocking.budgetHint') }}</p>
    </div>

    <div v-if="orderConfirmation" class="confirmation" role="status">
      <span>
        {{ t('restocking.orderPlaced', {
          orderNumber: orderConfirmation.order_number,
          date: formatDeliveryDate(orderConfirmation.expected_delivery)
        }) }}
      </span>
      <router-link to="/orders" class="confirmation-link">{{ t('restocking.viewOrders') }}</router-link>
    </div>
    <div v-if="orderError" class="error" role="alert">{{ orderError }}</div>

    <div v-if="error" class="error" role="alert">{{ error }}</div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">{{ t('restocking.totalCost') }}</div>
        <div class="stat-value">{{ formatCurrency(totalCost, currentCurrency) }}</div>
      </div>
      <div class="stat-card success">
        <div class="stat-label">{{ t('restocking.remainingBudget') }}</div>
        <div class="stat-value">{{ formatCurrency(remainingBudget, currentCurrency) }}</div>
      </div>
      <div class="stat-card info">
        <div class="stat-label">{{ t('restocking.leadTime') }}</div>
        <div class="stat-value">{{ t('restocking.days', { count: leadTimeDays }) }}</div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ items.length }})</h3>
        <button
          type="button"
          class="place-order-btn"
          :disabled="!canSubmit"
          @click="placeOrder"
        >
          {{ submitting ? t('restocking.submitting') : t('restocking.placeOrder') }}
        </button>
      </div>

      <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
      <div v-else-if="items.length === 0" class="empty-state">{{ t('restocking.empty') }}</div>
      <div v-else class="table-container">
        <table>
          <thead>
            <tr>
              <th>{{ t('restocking.table.sku') }}</th>
              <th>{{ t('restocking.table.name') }}</th>
              <th>{{ t('restocking.table.trend') }}</th>
              <th class="num">{{ t('restocking.table.quantity') }}</th>
              <th class="num">{{ t('restocking.table.unitCost') }}</th>
              <th class="num">{{ t('restocking.table.lineCost') }}</th>
              <th class="num">{{ t('restocking.table.leadTime') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.sku">
              <td><strong>{{ item.sku }}</strong></td>
              <td>{{ translateProductName(item.name) }}</td>
              <td>
                <span :class="['badge', item.trend]">{{ translateTrend(item.trend) }}</span>
              </td>
              <td class="num">{{ item.quantity }}</td>
              <td class="num">{{ formatCurrencyWithDecimals(item.unit_cost, currentCurrency, 2) }}</td>
              <td class="num"><strong>{{ formatCurrencyWithDecimals(item.line_cost, currentCurrency, 2) }}</strong></td>
              <td class="num">{{ t('restocking.days', { count: item.lead_time_days }) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

const BUDGET_MIN = 0
const BUDGET_MAX = 50000
const BUDGET_STEP = 500
const DEBOUNCE_MS = 300

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, currentLocale, translateProductName } = useI18n()

    const budget = ref(10000) // USD
    const items = ref([])
    const totalCost = ref(0)
    const remainingBudget = ref(0)
    const leadTimeDays = ref(0)

    const loading = ref(true)
    const error = ref(null)
    const submitting = ref(false)
    const orderError = ref(null)
    const orderConfirmation = ref(null)

    let debounceTimer = null
    let requestId = 0

    const loadRecommendations = async () => {
      const myId = ++requestId
      loading.value = true
      error.value = null
      try {
        const data = await api.getRestockingRecommendations(budget.value)
        if (myId !== requestId) return // stale response
        items.value = Array.isArray(data.items) ? data.items : []
        totalCost.value = data.total_cost ?? 0
        remainingBudget.value = data.remaining_budget ?? 0
        leadTimeDays.value = data.lead_time_days ?? 0
      } catch (err) {
        if (myId !== requestId) return
        console.error('Failed to load recommendations:', err)
        items.value = []
        totalCost.value = 0
        remainingBudget.value = 0
        leadTimeDays.value = 0
        error.value = t('restocking.loadError')
      } finally {
        if (myId === requestId) loading.value = false
      }
    }

    watch(budget, () => {
      orderError.value = null
      // Mark current data as stale right away so it cannot be submitted
      loading.value = true
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(loadRecommendations, DEBOUNCE_MS)
    })

    const canSubmit = computed(() => items.value.length > 0 && !submitting.value && !loading.value)

    const placeOrder = async () => {
      if (!canSubmit.value) return
      submitting.value = true
      orderError.value = null
      orderConfirmation.value = null
      try {
        const order = await api.createRestockOrder({
          items: items.value.map(i => ({ sku: i.sku, quantity: i.quantity })),
          budget: budget.value
        })
        orderConfirmation.value = order
        await loadRecommendations()
      } catch (err) {
        orderError.value = err.response?.data?.detail || t('restocking.orderFailed')
        if (typeof orderError.value !== 'string') {
          orderError.value = t('restocking.orderFailed')
        }
      } finally {
        submitting.value = false
      }
    }

    const translateTrend = (trend) => {
      const key = `trends.${trend}`
      const label = t(key)
      return label === key ? trend : label
    }

    const formatDeliveryDate = (dateString) => {
      if (!dateString) return ''
      const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(dateString) ? `${dateString}T00:00:00` : dateString)
      if (isNaN(date.getTime())) return dateString
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      return date.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' })
    }

    onMounted(loadRecommendations)
    onBeforeUnmount(() => {
      clearTimeout(debounceTimer)
      requestId++ // drop any in-flight response
    })

    return {
      t,
      currentCurrency,
      translateProductName,
      formatCurrency,
      formatCurrencyWithDecimals,
      BUDGET_MIN,
      BUDGET_MAX,
      BUDGET_STEP,
      budget,
      items,
      totalCost,
      remainingBudget,
      leadTimeDays,
      loading,
      error,
      submitting,
      orderError,
      orderConfirmation,
      canSubmit,
      placeOrder,
      translateTrend,
      formatDeliveryDate
    }
  }
}
</script>

<style scoped>
.budget-card {
  padding: 1.5rem;
}

.budget-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.75rem;
}

.budget-label {
  color: #64748b;
  font-size: 0.875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.budget-value {
  font-size: 1.75rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
}

.budget-slider {
  width: 100%;
  accent-color: #0f172a;
  cursor: pointer;
}

.budget-slider:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.budget-scale {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: #64748b;
  margin-top: 0.25rem;
}

.budget-hint {
  margin-top: 0.75rem;
  font-size: 0.813rem;
  color: #64748b;
}

.confirmation {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
  padding: 1rem;
  border-radius: 8px;
  margin: 1rem 0;
  font-size: 0.938rem;
}

.confirmation-link {
  color: #0f172a;
  font-weight: 600;
  text-decoration: underline;
  white-space: nowrap;
}

.place-order-btn {
  background: #0f172a;
  color: white;
  border: 1px solid #0f172a;
  border-radius: 6px;
  padding: 0.5rem 1rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease;
}

.place-order-btn:hover:not(:disabled) {
  background: #1e293b;
}

.place-order-btn:disabled {
  background: #e2e8f0;
  border-color: #e2e8f0;
  color: #94a3b8;
  cursor: not-allowed;
}

.empty-state {
  text-align: center;
  padding: 2rem;
  color: #64748b;
  font-size: 0.938rem;
}

th.num,
td.num {
  text-align: right;
}
</style>
