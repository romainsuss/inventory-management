"""
Tests for restocking API endpoints.
"""
import pytest

from mock_data import restock_orders


@pytest.fixture(autouse=True)
def clear_restock_orders():
    """Submitted orders live in memory, so reset them around every test."""
    restock_orders.clear()
    yield
    restock_orders.clear()


class TestRestockRecommendationsEndpoint:
    """Test suite for the restocking recommendations endpoint."""

    def test_zero_budget_returns_no_items(self, client):
        """Test that a zero budget recommends nothing."""
        response = client.get("/api/restocking/recommendations?budget=0")
        assert response.status_code == 200

        data = response.json()
        assert data["items"] == []
        assert data["total_cost"] == 0
        assert data["lead_time_days"] == 0

    def test_recommendation_structure(self, client):
        """Test that recommendations have the expected structure."""
        response = client.get("/api/restocking/recommendations?budget=10000")
        assert response.status_code == 200

        data = response.json()
        for field in ["budget", "items", "total_cost", "remaining_budget", "lead_time_days"]:
            assert field in data
        assert len(data["items"]) > 0

        for item in data["items"]:
            for field in ["sku", "name", "trend", "quantity", "unit_cost", "line_cost", "lead_time_days"]:
                assert field in item
            assert isinstance(item["quantity"], int)
            assert item["quantity"] > 0

    @pytest.mark.parametrize("budget", [100, 1000, 5000, 20000, 1000000])
    def test_total_never_exceeds_budget(self, client, budget):
        """Test that the recommended spend stays within the budget."""
        data = client.get(f"/api/restocking/recommendations?budget={budget}").json()

        assert data["total_cost"] <= budget
        assert abs(data["total_cost"] + data["remaining_budget"] - budget) < 0.01
        line_total = sum(item["line_cost"] for item in data["items"])
        assert abs(line_total - data["total_cost"]) < 0.01

    def test_decreasing_trend_items_excluded(self, client):
        """Test that items with decreasing demand are never recommended."""
        data = client.get("/api/restocking/recommendations?budget=1000000").json()

        assert all(item["trend"] != "decreasing" for item in data["items"])
        assert "MTR-304" not in [item["sku"] for item in data["items"]]

    def test_quantity_capped_at_forecast_demand_gap(self, client):
        """Test that a large budget recommends exactly the demand gap per item."""
        forecasts = {f["item_sku"]: f for f in client.get("/api/demand").json()}
        data = client.get("/api/restocking/recommendations?budget=1000000").json()

        for item in data["items"]:
            forecast = forecasts[item["sku"]]
            assert item["quantity"] == forecast["forecasted_demand"] - forecast["current_demand"]

    def test_items_ordered_by_demand_gap(self, client):
        """Test that the largest demand gap is filled first."""
        forecasts = {f["item_sku"]: f for f in client.get("/api/demand").json()}
        data = client.get("/api/restocking/recommendations?budget=1000000").json()

        gaps = [
            forecasts[i["sku"]]["forecasted_demand"] - forecasts[i["sku"]]["current_demand"]
            for i in data["items"]
        ]
        assert gaps == sorted(gaps, reverse=True)

    def test_lead_time_is_longest_item_lead_time(self, client):
        """Test that the order lead time is the slowest item's lead time."""
        data = client.get("/api/restocking/recommendations?budget=1000000").json()

        assert data["lead_time_days"] == max(item["lead_time_days"] for item in data["items"])

    def test_negative_budget_rejected(self, client):
        """Test that a negative budget fails validation."""
        response = client.get("/api/restocking/recommendations?budget=-1")
        assert response.status_code == 422


class TestRestockOrdersEndpoint:
    """Test suite for submitting and listing restocking orders."""

    def test_no_orders_initially(self, client):
        """Test that the submitted orders list starts empty."""
        response = client.get("/api/restocking/orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_order_and_list_it(self, client):
        """Test that a submitted order is returned and then listed."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 100}], "budget": 5000},
        )
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"].startswith("RST-")
        assert order["status"] == "Submitted"
        assert order["total_cost"] == 1250.0
        assert order["lead_time_days"] == 14
        assert order["expected_delivery"] > order["submitted_date"][:10]

        listed = client.get("/api/restocking/orders").json()
        assert [o["id"] for o in listed] == [order["id"]]

    def test_order_lead_time_is_longest_item(self, client):
        """Test that a multi-item order takes the slowest item's lead time."""
        response = client.post(
            "/api/restocking/orders",
            json={
                "items": [{"sku": "FLT-405", "quantity": 10}, {"sku": "WDG-001", "quantity": 10}],
                "budget": 5000,
            },
        )
        assert response.status_code == 201
        assert response.json()["lead_time_days"] == 14

    def test_orders_listed_newest_first(self, client):
        """Test that the newest order comes first."""
        for _ in range(2):
            client.post(
                "/api/restocking/orders",
                json={"items": [{"sku": "GSK-203", "quantity": 1}], "budget": 100},
            )

        listed = client.get("/api/restocking/orders").json()
        assert [o["id"] for o in listed] == ["2", "1"]

    def test_empty_order_rejected(self, client):
        """Test that an order with no items is rejected."""
        response = client.post("/api/restocking/orders", json={"items": [], "budget": 1000})
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_over_budget_order_rejected(self, client):
        """Test that an order costing more than its budget is rejected."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 100}], "budget": 1000},
        )
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()
        assert client.get("/api/restocking/orders").json() == []

    def test_unknown_sku_rejected(self, client):
        """Test that an unknown SKU is rejected."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "NOPE-000", "quantity": 1}], "budget": 1000},
        )
        assert response.status_code == 400
        assert "unknown sku" in response.json()["detail"].lower()

    def test_non_positive_quantity_rejected(self, client):
        """Test that a zero quantity fails validation."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 0}], "budget": 1000},
        )
        assert response.status_code == 422

    def test_client_supplied_price_ignored(self, client):
        """Test that prices come from forecast data, not the request body."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 10, "unit_cost": 0.01}], "budget": 1000},
        )
        assert response.status_code == 201
        assert response.json()["items"][0]["unit_cost"] == 12.5
        assert response.json()["total_cost"] == 125.0
