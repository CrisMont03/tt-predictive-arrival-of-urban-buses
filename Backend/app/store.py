from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock


class MemoryStore:
    """Only for demonstration and tests. Never selected in production."""

    def __init__(self):
        self.cache = {}
        self.queries = {}
        self.reports = {}
        self.lock = RLock()

    def get_cache(self, key):
        return deepcopy(self.cache.get(key))

    def set_cache(self, key, value):
        self.cache[key] = deepcopy(value)

    def get_query(self, uid, query_id):
        return deepcopy(self.queries.get((uid, query_id)))

    def create_query(self, uid, query_id, value):
        with self.lock:
            return deepcopy(self.queries.setdefault((uid, query_id), deepcopy(value)))

    def save_feedback(self, uid, query_id, value):
        with self.lock:
            existing = self.reports.get((uid, query_id))
            if existing and existing["actual_min"] != value["actual_min"]:
                raise ValueError("Ya existe un reporte diferente para esta consulta.")
            self.reports.setdefault((uid, query_id), deepcopy(value))
            self.queries[(uid, query_id)]["actual_min"] = value["actual_min"]
            return deepcopy(self.reports[(uid, query_id)])

    def recent_observations(self, station_id, since):
        return [deepcopy(item) for item in self.reports.values() if item["station_id"] == station_id and not item["simulation"] and item["received_at"] >= since and not item["outside_domain"]]


class FirestoreStore:
    def __init__(self):
        from firebase_admin import firestore
        self.db = firestore.client()

    def get_cache(self, key):
        return self.db.collection("predictions").document(key).get().to_dict()

    def set_cache(self, key, value):
        self.db.collection("predictions").document(key).set({**value, "expiresAt": value["expires_at"]})

    def query_ref(self, uid, query_id):
        return self.db.collection("query_history").document(uid).collection("queries").document(query_id)

    def get_query(self, uid, query_id):
        return self.query_ref(uid, query_id).get().to_dict()

    def create_query(self, uid, query_id, value):
        from google.api_core.exceptions import AlreadyExists
        ref = self.query_ref(uid, query_id)
        try:
            ref.create(value)
            return value
        except AlreadyExists:
            return ref.get().to_dict()

    def save_feedback(self, uid, query_id, value):
        from firebase_admin import firestore
        ref = self.db.collection("feedback").document(uid).collection("entries").document(query_id)
        history_ref = self.query_ref(uid, query_id)

        @firestore.transactional
        def save(transaction):
            existing = ref.get(transaction=transaction).to_dict()
            if existing and existing["actual_min"] != value["actual_min"]:
                raise ValueError("Ya existe un reporte diferente para esta consulta.")
            if not existing:
                transaction.set(ref, value)
            transaction.update(history_ref, {"actual_min": value["actual_min"]})
            return existing or value
        return save(self.db.transaction())

    def recent_observations(self, station_id, since):
        from google.cloud.firestore_v1.base_query import FieldFilter
        return [item.to_dict() for item in self.db.collection_group("entries").where(filter=FieldFilter("station_id", "==", station_id)).where(filter=FieldFilter("simulation", "==", False)).where(filter=FieldFilter("received_at", ">=", since)).stream() if not item.to_dict().get("outside_domain")]
