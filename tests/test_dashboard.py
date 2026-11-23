"""Tests for dashboard."""

import pytest
from pathlib import Path
from app.dashboard.app import create_app, validate_dashboard_host
from app.database.db import Database


@pytest.fixture
def test_db(tmp_path):
    """Create test database."""
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    with db:
        db.initialize()
    return db_path


@pytest.fixture
def client(test_db, tmp_path):
    """Create test client."""
    app = create_app(test_db, tmp_path, tmp_path)
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_dashboard_index(client):
    """Test dashboard index page."""
    response = client.get('/')
    assert response.status_code == 200
    assert b'Enlivonex YouTube Automation' in response.data


def test_api_status(client):
    """Test status API endpoint."""
    response = client.get('/api/status')
    assert response.status_code == 200

    data = response.get_json()
    assert 'clips' in data
    assert 'jobs' in data
    assert 'uploaded_videos' in data


def test_dashboard_api_does_not_allow_cross_origin_reads(client):
    response = client.get(
        "/api/status", headers={"Origin": "https://attacker.example"}
    )

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "localhost"),
        ("::1", "::1"),
    ],
)
def test_dashboard_host_accepts_loopback_only(host, expected):
    assert validate_dashboard_host(host) == expected


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "example.com"])
def test_dashboard_host_rejects_network_bindings(host):
    with pytest.raises(ValueError, match="only to localhost"):
        validate_dashboard_host(host)


def test_api_clips(client):
    """Test clips API endpoint."""
    response = client.get('/api/clips')
    assert response.status_code == 200

    data = response.get_json()
    assert isinstance(data, list)


def test_api_clips_with_limit(client):
    """Test clips API with limit parameter."""
    response = client.get('/api/clips?limit=5')
    assert response.status_code == 200

    data = response.get_json()
    assert len(data) <= 5


def test_api_jobs(client):
    """Test jobs API endpoint."""
    response = client.get('/api/jobs')
    assert response.status_code == 200

    data = response.get_json()
    assert isinstance(data, list)


def test_api_job_details_not_found(client):
    """Test job details for nonexistent job."""
    response = client.get('/api/jobs/NONEXISTENT')
    assert response.status_code == 404


def test_api_analytics(client):
    """Test analytics API endpoint."""
    response = client.get('/api/analytics')
    assert response.status_code == 200

    data = response.get_json()
    assert isinstance(data, list)
