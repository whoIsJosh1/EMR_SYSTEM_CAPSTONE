# ============================================================
# routers/diseases.py — Dynamic disease management
# - Admin CRUD ng sakit (hindi na hardcoded)
# - Symptom master list + disease_symptom weights
# - ICD-10 search (proxy sa server dahil 'connect-src self' ang CSP)
# - Symptom-based detection (DB-driven)
# ============================================================
import re
from typing import Optional, List

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from routers.symptom_vocab import SYMPTOM_VOCAB
from middleware.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/diseases", tags=["Diseases"])

KEY_BONUS = 5.0   # dagdag na % sa score kada key symptom na tumugma


# ───────────────────────── Schema bootstrap ─────────────────────────
def ensure_schema(engine):
    """Awtomatikong gumagawa ng bagong tables/columns kung wala pa.
    Safe i-run kahit ilang beses. Tatakbo sa bawat barangay database."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS symptom (
                symptom_id   INT AUTO_INCREMENT PRIMARY KEY,
                symptom_name VARCHAR(100) NOT NULL UNIQUE,
                synonyms     VARCHAR(255) NULL,
                is_active    TINYINT(1) NOT NULL DEFAULT 1
            )"""))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS disease_symptom (
                disease_id     INT NOT NULL,
                symptom_id     INT NOT NULL,
                weight         DECIMAL(3,2) NOT NULL DEFAULT 1.00,
                is_key_symptom TINYINT(1) NOT NULL DEFAULT 0,
                PRIMARY KEY (disease_id, symptom_id)
            )"""))
        existing = {r[0] for r in conn.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'disease'"))}
        new_cols = {
            "aliases":     "aliases VARCHAR(500) NULL",
            "description": "description TEXT NULL",
            "is_active":   "is_active TINYINT(1) NOT NULL DEFAULT 1",
            "source":      "source ENUM('manual','icd_import') NOT NULL DEFAULT 'manual'",
        }
        for name, ddl in new_cols.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE disease ADD COLUMN {ddl}"))
    _seed_defaults(engine)


# Aliases = mga salitang hahanapin sa diagnosis text (galing sa dating regex)
_ALIASES = {
    "J11": "flu,gripe", "A90": "dengue,dhf,dengue hemorrhagic",
    "A15": "tb,pulmonary tb", "U07.1": "covid19,covid-19,coronavirus,sars-cov",
    "I10": "htn,high blood pressure", "E11": "diabetic,dm,dm type",
    "J18": "pneumonitis", "A09": "diarrhoea,gastroenteritis,lbm",
    "A01": "typhoid,enteric fever", "J06": "ari,urti,nasopharyngitis",
    "B01": "varicella", "B05": "tigdas,rubeola", "J45": "bronchial asthma",
    "E46": "malnourished",
}

_SYMPTOMS = [
    ("Fever", "lagnat,mainit ang katawan"), ("Cough", "ubo"),
    ("Headache", "sakit ng ulo"), ("Muscle pain", "sakit ng katawan,myalgia"),
    ("Rash", "pantal"), ("Itching", "pangangati"),
    ("Diarrhea", "pagtatae,lbm"), ("Vomiting", "pagsusuka"),
    ("Shortness of breath", "hirap huminga"), ("Joint pain", "sakit ng kasukasuan"),
    ("Eye pain", "sakit sa likod ng mata"), ("Sore throat", "masakit ang lalamunan"),
    ("Runny nose", "sipon"), ("Abdominal pain", "sakit ng tiyan"),
    ("Chest pain", "sakit ng dibdib"), ("Weight loss", "pagpayat"),
    ("Night sweats", "pagpapawis sa gabi"), ("Fatigue", "pagod,panghihina"),
    ("Wheezing", "humuhuni ang dibdib"), ("Dizziness", "pagkahilo"),
    ("Excessive thirst", "labis na pagkauhaw"), ("Frequent urination", "madalas na pag-ihi"),
    ("Bleeding", "pagdurugo"), ("Red eyes", "pamumula ng mata"), ("Jaundice", "paninilaw"),
]

# icd_code: [(symptom, weight, is_key)]  — SAMPLE LANG, ipa-validate sa doktor/midwife
_MAP = {
    "A90": [("Fever",1,1),("Eye pain",.8,1),("Headache",.6,0),("Muscle pain",.7,0),("Joint pain",.7,0),("Rash",.6,0),("Bleeding",.8,1)],
    "J11": [("Fever",1,1),("Cough",.9,0),("Sore throat",.6,0),("Runny nose",.6,0),("Muscle pain",.7,0),("Headache",.5,0),("Fatigue",.5,0)],
    "A15": [("Cough",1,1),("Weight loss",.8,0),("Night sweats",.8,0),("Fever",.6,0),("Fatigue",.5,0),("Chest pain",.5,0)],
    "U07.1": [("Fever",.8,0),("Cough",.9,0),("Sore throat",.5,0),("Shortness of breath",.8,1),("Fatigue",.5,0),("Headache",.4,0)],
    "I10": [("Headache",.6,0),("Dizziness",.7,0),("Chest pain",.5,0)],
    "E11": [("Excessive thirst",1,1),("Frequent urination",1,1),("Fatigue",.5,0),("Weight loss",.6,0)],
    "J18": [("Cough",1,1),("Shortness of breath",.9,1),("Fever",.8,0),("Chest pain",.6,0),("Fatigue",.4,0)],
    "A09": [("Diarrhea",1,1),("Abdominal pain",.7,0),("Vomiting",.6,0),("Fever",.4,0)],
    "A27": [("Fever",1,1),("Muscle pain",.9,1),("Headache",.6,0),("Red eyes",.7,0),("Jaundice",.6,0),("Vomiting",.4,0)],
    "A01": [("Fever",1,1),("Abdominal pain",.7,0),("Headache",.6,0),("Fatigue",.5,0),("Diarrhea",.4,0)],
    "J06": [("Cough",1,0),("Runny nose",.9,0),("Sore throat",.8,0),("Fever",.5,0)],
    "B01": [("Rash",1,1),("Itching",.8,0),("Fever",.6,0),("Fatigue",.3,0)],
    "B05": [("Fever",1,0),("Rash",1,1),("Red eyes",.8,1),("Cough",.7,0),("Runny nose",.6,0)],
    "J45": [("Wheezing",1,1),("Shortness of breath",1,1),("Cough",.7,0),("Chest pain",.4,0)],
    "E46": [("Weight loss",.9,1),("Fatigue",.6,0)],
}


def _seed_defaults(engine):
    with engine.begin() as conn:
        for name, syn in _SYMPTOMS:
            conn.execute(text("INSERT IGNORE INTO symptom (symptom_name, synonyms) VALUES (:n,:s)"),
                         {"n": name, "s": syn})
        for code, al in _ALIASES.items():
            conn.execute(text("UPDATE disease SET aliases=:a WHERE icd_code=:c AND (aliases IS NULL OR aliases='')"),
                         {"a": al, "c": code})
        has_map = conn.execute(text("SELECT COUNT(*) FROM disease_symptom")).scalar()
        if has_map:
            return  # huwag galawin kung may laman na (baka na-edit na ng admin)
        for code, items in _MAP.items():
            d = conn.execute(text("SELECT disease_id FROM disease WHERE icd_code=:c"), {"c": code}).scalar()
            if not d:
                continue
            for sname, w, key in items:
                s = conn.execute(text("SELECT symptom_id FROM symptom WHERE symptom_name=:n"), {"n": sname}).scalar()
                if s:
                    conn.execute(text("INSERT IGNORE INTO disease_symptom (disease_id,symptom_id,weight,is_key_symptom) "
                                      "VALUES (:d,:s,:w,:k)"), {"d": d, "s": s, "w": w, "k": key})


# ───────────────────────── Schemas ─────────────────────────
_ICD_RE = re.compile(r"^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")


class DiseaseIn(BaseModel):
    disease_name: str = Field(..., min_length=2, max_length=150)
    icd_code: Optional[str] = Field(None, max_length=8)
    category: str = "Communicable"
    is_notifiable: bool = False
    aliases: Optional[str] = None
    description: Optional[str] = None
    source: str = "manual"

    @field_validator("icd_code")
    @classmethod
    def valid_icd(cls, v):
        # ICD-10: 1 letra + 1 numero + 1 alphanumeric, opsyonal na "." + 1–4 alphanumeric (hal. B86, U07.1, S72.001A)
        v = (v or "").strip().upper()
        if not v:
            return None
        if not _ICD_RE.match(v):
            raise ValueError("Invalid ICD-10 code format (e.g. B86, U07.1, E11.9).")
        return v


class SymptomIn(BaseModel):
    symptom_name: str = Field(..., min_length=2, max_length=100)

    @field_validator("symptom_name")
    @classmethod
    def clean_name(cls, v):
        return " ".join((v or "").split())


class MapItem(BaseModel):
    symptom_id: int
    weight: float = Field(1.0, ge=0.1, le=1.0)   # iniimbak bilang 0.1–1.0 (UI: 1–10)
    is_key_symptom: bool = False

    @field_validator("weight")
    @classmethod
    def whole_number_scale(cls, v):
        # dapat katumbas ng buong numero 1–10 (0.1, 0.2, ... 1.0)
        if abs(v * 10 - round(v * 10)) > 1e-6:
            raise ValueError("Weight must be a whole number from 1 to 10.")
        return round(v, 1)


class MapIn(BaseModel):
    items: List[MapItem]


class DetectIn(BaseModel):
    symptom_ids: List[int] = Field(default_factory=list)


# ───────────────────── Symptom vocabulary (PUMILI lang, hindi libreng pag-type) ─────────────────────
# Ang bagong sintomas ay dapat galing sa:
#   1) SYMPTOM_VOCAB (routers/symptom_vocab.py) — English + Tagalog, laging available, o
#   2) HPO (Human Phenotype Ontology) via NLM — kailangan ng internet
# Ang anumang hindi makita sa dalawa ay tinatanggihan, kahit direktang tawag sa API.
HPO_URL = "https://clinicaltables.nlm.nih.gov/api/hpo/v3/search"


def _key(s: Optional[str]) -> str:
    """Susi sa paghahambing: lowercase, walang puwang/simbolo."""
    return re.sub(r"[\W_]+", "", (s or "").casefold())


def normalize_synonyms(items, name: Optional[str] = None) -> Optional[str]:
    nk, seen, out = _key(name), set(), []
    for t in items or []:
        t = " ".join(str(t).split()).lower()
        if not t or t in seen or (nk and _key(t) == nk):
            continue
        seen.add(t)
        out.append(t)
    return ",".join(out) or None


_LOCAL = {_key(n): (n, syn) for n, syn in SYMPTOM_VOCAB}


def _local_matches(q: str):
    """Mga sintomas sa lokal na listahan na tumutugma sa tinype (pangalan o Tagalog na tawag)."""
    qk, out = _key(q), []
    if not qk:
        return out
    for name, syn in SYMPTOM_VOCAB:
        nk, sk = _key(name), [(_key(x), x) for x in syn]
        via, score = None, None
        if nk.startswith(qk):
            score = 0
        elif any(k.startswith(qk) for k, _ in sk):
            score, via = 1, next(x for k, x in sk if k.startswith(qk))
        elif qk in nk:
            score = 2
        elif any(qk in k for k, _ in sk):
            score, via = 3, next(x for k, x in sk if qk in k)
        if score is not None:
            out.append((score, name, via))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def _hpo_names(term: str, limit: int = 10) -> List[str]:
    """Mga pangalan ng HPO term na tumutugma. RuntimeError kung hindi maabot ang serbisyo."""
    try:
        r = requests.get(HPO_URL, params={"terms": term, "maxList": limit}, timeout=6)
        r.raise_for_status()
        rows = r.json()[3] or []
    except Exception as exc:
        raise RuntimeError("HPO unavailable") from exc
    names = []
    for row in rows:
        n = row[0] if isinstance(row, (list, tuple)) and row else row
        if isinstance(n, str):
            n = " ".join(n.split())
            if 2 <= len(n) <= 100:
                names.append(n)
    return names


def _clean(s: Optional[str]) -> Optional[str]:
    s = (s or "").strip()
    return s or None


# ───────────────────────── Endpoints ─────────────────────────
# NOTE: static routes muna bago ang "/{disease_id}"

@router.get("/")
def list_active(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Active na sakit lang — para sa dropdowns at forms (dating endpoint, compatible)."""
    rows = db.execute(text(
        "SELECT disease_id, disease_name, icd_code, category, is_notifiable "
        "FROM disease WHERE is_active = 1 ORDER BY disease_name")).mappings().all()
    return [dict(r, is_notifiable=bool(r["is_notifiable"])) for r in rows]


@router.get("/admin/list")
def list_all(db: Session = Depends(get_db), current_user=Depends(require_admin)):
    rows = db.execute(text("""
        SELECT d.disease_id, d.disease_name, d.icd_code, d.category, d.is_notifiable,
               d.aliases, d.description, d.is_active, d.source,
               (SELECT COUNT(*) FROM disease_symptom ds WHERE ds.disease_id = d.disease_id) AS symptom_count
        FROM disease d ORDER BY d.is_active DESC, d.disease_name""")).mappings().all()
    return [dict(r, is_notifiable=bool(r["is_notifiable"]), is_active=bool(r["is_active"])) for r in rows]


@router.get("/symptoms")
def list_symptoms(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = db.execute(text(
        "SELECT symptom_id, symptom_name, synonyms FROM symptom WHERE is_active=1 ORDER BY symptom_name")).mappings().all()
    return [dict(r) for r in rows]


@router.get("/symptoms/search")
def search_symptom_vocab(q: str = Query(..., min_length=2, max_length=60), db: Session = Depends(get_db),
                         current_user=Depends(require_admin)):
    """Mga mungkahing sintomas na mapipili ng admin: lokal na listahan muna, kasunod ang HPO (online)."""
    existing = {_key(r["symptom_name"]): (r["symptom_id"], r["symptom_name"])
                for r in db.execute(text("SELECT symptom_id, symptom_name FROM symptom")).mappings().all()}

    def row(name, source, note=None):
        hit = existing.get(_key(name))
        return {"name": name, "source": source, "note": note,
                "exists_id": hit[0] if hit else None, "exists_name": hit[1] if hit else None}

    results, seen = [], set()
    for _score, name, via in _local_matches(q)[:8]:
        results.append(row(name, "local", via))
        seen.add(_key(name))

    online = True
    if len(_key(q)) >= 3:
        try:
            for n in _hpo_names(q, 10):
                if _key(n) not in seen and len(results) < 16:
                    results.append(row(n, "hpo"))
                    seen.add(_key(n))
        except RuntimeError:
            online = False
    return {"results": results, "online": online}


@router.post("/symptoms")
def add_symptom(body: SymptomIn, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """Idagdag ang sintomas LAMANG kung nasa lokal na listahan o sa HPO. Hindi tinatanggap ang random na salita."""
    k = _key(body.symptom_name)
    synonyms = None
    if k in _LOCAL:
        name, syn = _LOCAL[k]
        synonyms = normalize_synonyms(syn, name)             # isama ang Tagalog para mahanap ng doktor
    else:
        try:
            matches = [n for n in _hpo_names(body.symptom_name, 25) if _key(n) == k]
        except RuntimeError:
            raise HTTPException(503, "Cannot verify this symptom without an internet connection. "
                                     "Pick one from the local list, or try again when online.")
        if not matches:
            raise HTTPException(422, f'"{body.symptom_name}" was not found in the medical vocabulary. '
                                     "Search and pick a symptom from the suggestions.")
        name = matches[0]

    dup = db.execute(text("SELECT symptom_id, symptom_name FROM symptom WHERE LOWER(symptom_name)=LOWER(:n)"),
                     {"n": name}).mappings().first()
    if not dup:
        for r in db.execute(text("SELECT symptom_id, symptom_name FROM symptom")).mappings().all():
            if _key(r["symptom_name"]) == _key(name):
                dup = r
                break
    if dup:
        raise HTTPException(409, {"code": "duplicate", "symptom_id": dup["symptom_id"],
                                  "symptom_name": dup["symptom_name"], "message": f'"{dup["symptom_name"]}" already exists.'})
    try:
        res = db.execute(text("INSERT INTO symptom (symptom_name, synonyms) VALUES (:n,:s)"), {"n": name, "s": synonyms})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, {"code": "duplicate_generic", "message": "This symptom already exists."})
    return {"symptom_id": res.lastrowid, "symptom_name": name, "synonyms": synonyms}


@router.get("/icd/search")
def icd_search(q: str = Query(..., min_length=3), current_user=Depends(require_admin)):
    """ICD-10-CM search via NLM Clinical Tables (libre, walang API key).
    Dito dumadaan sa server dahil ang CSP ng app ay 'connect-src self'."""
    try:
        r = requests.get(
            "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search",
            params={"sf": "code,name", "terms": q, "maxList": 10}, timeout=8)
        r.raise_for_status()
        data = r.json()
        return [{"icd_code": c, "name": n} for c, n in (data[3] or [])]
    except Exception:
        raise HTTPException(502, "Hindi maabot ang ICD service (offline?). Pwede pa ring i-type nang manual.")


@router.post("/detect")
def detect(body: DetectIn, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Top 5 posibleng sakit base sa piniling sintomas. Puro DB, walang hardcoded na sakit."""
    if not body.symptom_ids:
        return []
    picked = set(body.symptom_ids)
    rows = db.execute(text("""
        SELECT d.disease_id, d.disease_name, d.icd_code, d.is_notifiable,
               ds.symptom_id, ds.weight, ds.is_key_symptom, s.symptom_name
        FROM disease d
        JOIN disease_symptom ds ON ds.disease_id = d.disease_id
        JOIN symptom s ON s.symptom_id = ds.symptom_id
        WHERE d.is_active = 1""")).mappings().all()
    agg = {}
    for r in rows:
        a = agg.setdefault(r["disease_id"], {
            "disease_id": r["disease_id"], "disease_name": r["disease_name"],
            "icd_code": r["icd_code"], "is_notifiable": bool(r["is_notifiable"]),
            "matched": 0.0, "total": 0.0, "matched_symptoms": [], "key_symptoms": [], "key_hits": 0})
        w = float(r["weight"])
        a["total"] += w
        if r["symptom_id"] in picked:
            a["matched"] += w
            a["matched_symptoms"].append(r["symptom_name"])
            if r["is_key_symptom"]:
                a["key_hits"] += 1
                a["key_symptoms"].append(r["symptom_name"])
    out = []
    for a in agg.values():
        if a["matched"] > 0:
            base = a["matched"] / a["total"] * 100
            # Bonus: +KEY_BONUS bawat hallmark ("key") symptom na tumugma, max 100%
            a["score"] = round(min(100.0, base + KEY_BONUS * a["key_hits"]), 1)
            a["matched"] = round(a["matched"], 2)
            a["total"] = round(a["total"], 2)
            out.append(a)
    out.sort(key=lambda x: (x["score"], x["key_hits"]), reverse=True)
    return out[:5]


@router.post("/")
def create_disease(body: DiseaseIn, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    name, code = body.disease_name.strip(), _clean(body.icd_code)
    dup = db.execute(text(
        "SELECT disease_id FROM disease WHERE LOWER(disease_name)=LOWER(:n) OR (:c IS NOT NULL AND icd_code=:c)"),
        {"n": name, "c": code}).scalar()
    if dup:
        raise HTTPException(409, "Existing na ang sakit na ito (pangalan o ICD code).")
    res = db.execute(text("""
        INSERT INTO disease (disease_name, icd_code, category, is_notifiable, aliases, description, source)
        VALUES (:n,:c,:cat,:nt,:al,:ds,:src)"""),
        {"n": name, "c": code, "cat": body.category, "nt": int(body.is_notifiable),
         "al": _clean(body.aliases), "ds": _clean(body.description),
         "src": "icd_import" if body.source == "icd_import" else "manual"})
    db.commit()
    return {"disease_id": res.lastrowid, "message": "Disease added."}


@router.put("/{disease_id}")
def update_disease(disease_id: int, body: DiseaseIn, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    if not db.execute(text("SELECT 1 FROM disease WHERE disease_id=:i"), {"i": disease_id}).scalar():
        raise HTTPException(404, "Disease not found.")
    name, code = body.disease_name.strip(), _clean(body.icd_code)
    dup = db.execute(text(
        "SELECT disease_id FROM disease WHERE disease_id<>:i AND (LOWER(disease_name)=LOWER(:n) OR (:c IS NOT NULL AND icd_code=:c))"),
        {"i": disease_id, "n": name, "c": code}).scalar()
    if dup:
        raise HTTPException(409, "May ibang sakit na may parehong pangalan o ICD code.")
    db.execute(text("""
        UPDATE disease SET disease_name=:n, icd_code=:c, category=:cat, is_notifiable=:nt,
               aliases=:al, description=:ds WHERE disease_id=:i"""),
        {"n": name, "c": code, "cat": body.category, "nt": int(body.is_notifiable),
         "al": _clean(body.aliases), "ds": _clean(body.description), "i": disease_id})
    db.commit()
    return {"message": "Disease updated."}


@router.put("/{disease_id}/active")
def set_active(disease_id: int, active: bool, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """Deactivate imbes na burahin, para hindi masira ang lumang records."""
    res = db.execute(text("UPDATE disease SET is_active=:a WHERE disease_id=:i"), {"a": int(active), "i": disease_id})
    db.commit()
    if res.rowcount == 0:
        raise HTTPException(404, "Disease not found.")
    return {"message": "Status updated."}


@router.get("/{disease_id}/symptoms")
def get_disease_symptoms(disease_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    rows = db.execute(text(
        "SELECT symptom_id, weight, is_key_symptom FROM disease_symptom WHERE disease_id=:i"),
        {"i": disease_id}).mappings().all()
    return [{"symptom_id": r["symptom_id"], "weight": float(r["weight"]),
             "is_key_symptom": bool(r["is_key_symptom"])} for r in rows]


@router.put("/{disease_id}/symptoms")
def set_disease_symptoms(disease_id: int, body: MapIn, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    if not db.execute(text("SELECT 1 FROM disease WHERE disease_id=:i"), {"i": disease_id}).scalar():
        raise HTTPException(404, "Disease not found.")
    db.execute(text("DELETE FROM disease_symptom WHERE disease_id=:i"), {"i": disease_id})
    for it in body.items:
        db.execute(text("INSERT INTO disease_symptom (disease_id,symptom_id,weight,is_key_symptom) VALUES (:d,:s,:w,:k)"),
                   {"d": disease_id, "s": it.symptom_id, "w": it.weight, "k": int(it.is_key_symptom)})
    db.commit()
    return {"message": "Symptoms saved.", "count": len(body.items)}