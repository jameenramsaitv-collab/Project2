"""
Test script to verify FastAPI endpoints and routes in app.py.
"""

from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

def test_routes():
    print("=== Testing GET /api/status ===")
    res = client.get("/api/status")
    print("Status response:", res.status_code, res.json())
    assert res.status_code == 200
    assert "policies_available" in res.json()

    print("\n=== Testing GET /api/policies ===")
    res = client.get("/api/policies")
    print("Policies response:", res.status_code, [p["filename"] for p in res.json()["policies"]])
    assert res.status_code == 200
    assert len(res.json()["policies"]) >= 3

    print("\n=== Testing GET /api/emails ===")
    res = client.get("/api/emails")
    print("Emails count:", len(res.json()["emails"]))
    assert res.status_code == 200

    print("\n=== Testing GET / (Static index.html) ===")
    res = client.get("/")
    print("Root response:", res.status_code, "HTML length:", len(res.text))
    assert res.status_code == 200
    assert "Acme Corp HR Support" in res.text

    print("\nALL API ROUTE TESTS PASSED!")

if __name__ == "__main__":
    test_routes()

