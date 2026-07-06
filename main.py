from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header, Depends
from fastapi.responses import Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import time
import uuid
import shutil
import io
import base64
import numpy as np
import httpx
from urllib.parse import urlparse
from PIL import Image
from dotenv import load_dotenv
from supabase import create_client, Client

import rasterio
from rasterio.control import GroundControlPoint
from rasterio.transform import from_gcps
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject
from rasterio.io import MemoryFile

# ==========================================
# 1. SUPABASE INSTÄLLNINGAR
# ==========================================
load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Saknar Supabase-nycklar i .env!")

SUPABASE_URL = SUPABASE_URL.replace("/rest/v1/", "").replace("/rest/v1", "").rstrip("/")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def require_user(token: str) -> str:
    """Validerar en Supabase-token och returnerar user_id. Kastar 401 annars.
    Används av alla endpoints som kostar pengar/kvot att köra (georeferering,
    förhandsgranskning, Lantmäteriet-proxyn) - de får bara köras av inloggade
    användare, annars kan vem som helst som hittar den publika URL:en förbruka
    beräkningskraft, lagring och Lantmäteriets anropskvot gratis åt sig själva."""
    if not token or token == "null":
        raise HTTPException(status_code=401, detail="Inloggning krävs")
    try:
        auth_res = supabase.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Ogiltig eller utgången inloggning")
    if not auth_res or not auth_res.user:
        raise HTTPException(status_code=401, detail="Ogiltig eller utgången inloggning")
    return auth_res.user.id

def require_user_header(authorization: str = Header(None)) -> str:
    """Samma som require_user, men läser token från Authorization: Bearer-headern -
    för GET-endpoints som inte har formulärdata att lägga token i."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Inloggning krävs")
    return require_user(authorization.removeprefix("Bearer "))

# ==========================================
# 1b. LANTMÄTERIET NGP (OGC-Features / STAC) - HEMLIGHETER STANNAR HÄR I BACKEND
# ==========================================
# Consumer Key/Secret får ALDRIG skickas till eller nås av webbläsaren (index.html).
# De hämtas bara här på servern och används för att i sin tur hämta en kortlivad
# OAuth2 access token, som är det enda som (tillfälligt) skickas vidare vid varje
# proxad förfrågan - se lantmateriet_proxy() nedan.
LANTMATERIET_CONSUMER_KEY = os.getenv("LANTMATERIET_CONSUMER_KEY")
LANTMATERIET_CONSUMER_SECRET = os.getenv("LANTMATERIET_CONSUMER_SECRET")
LANTMATERIET_TOKEN_URL = "https://apimanager.lantmateriet.se/oauth2/token"

_lm_token_cache = {}  # nyckel: scope (eller "" om inget scope) -> {"access_token":..., "expires_at":...}

async def get_lantmateriet_token(scope: str = None) -> str:
    cache_key = scope or ""
    now = time.time()
    cached = _lm_token_cache.get(cache_key)
    if cached and cached["expires_at"] > now + 30:
        return cached["access_token"]

    if not LANTMATERIET_CONSUMER_KEY or not LANTMATERIET_CONSUMER_SECRET:
        raise HTTPException(status_code=500, detail="Lantmäteriet-nycklar saknas i .env (LANTMATERIET_CONSUMER_KEY/SECRET)")

    data = {"grant_type": "client_credentials"}
    if scope:
        data["scope"] = scope

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            LANTMATERIET_TOKEN_URL,
            data=data,
            auth=(LANTMATERIET_CONSUMER_KEY, LANTMATERIET_CONSUMER_SECRET),
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Kunde inte hämta token från Lantmäteriet: {resp.text}")
        token_data = resp.json()

    _lm_token_cache[cache_key] = {
        "access_token": token_data["access_token"],
        "expires_at": now + int(token_data.get("expires_in", 3600)),
    }
    return _lm_token_cache[cache_key]["access_token"]

# ==========================================
# 2. FASTAPI INSTÄLLNINGAR
# ==========================================
app = FastAPI(title="Fornkompassen API")

# Vilka domäner frontend får anropas ifrån - konfigureras i .env (kommaseparerat),
# istället för "*" som skulle tillåta vilken sida som helst på internet att anropa
# vårt API. Lägg till produktions-domänen i ALLOWED_ORIGINS när frontend är
# utlagd, utan att behöva ändra kod.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5500").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 3. FÖRHANDSGRANSKNING (BLIXTSNABB)
# ==========================================
@app.post("/api/preview")
async def create_preview(image: UploadFile = File(...), token: str = Form(...)):
    require_user(token)
    temp_path = f"temp_preview_{uuid.uuid4()}.tif"
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
            
        with rasterio.open(temp_path) as src:
            orig_w = src.width
            orig_h = src.height
            max_dim = 2500
            scale = min(max_dim / orig_w, max_dim / orig_h, 1.0)
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)
            
            data = src.read(out_shape=(src.count, new_h, new_w), resampling=Resampling.bilinear)
            
            if src.count >= 3:
                img_array = np.moveaxis(data[:3], 0, -1)
            else:
                img_array = data[0]
                
            pil_img = Image.fromarray(img_array)
            if pil_img.mode != 'RGB': pil_img = pil_img.convert('RGB')
                
            buffered = io.BytesIO()
            pil_img.save(buffered, format="JPEG", quality=80)
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            
            return {
                "status": "success",
                "original_width": orig_w,
                "original_height": orig_h,
                "base64_image": img_str
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# ==========================================
# 4. GEOREFERERING, AUTH & EXPORT
# ==========================================
@app.post("/api/georeference")
async def process_map(
    image: UploadFile = File(...),
    gcp_data: str = Form(...),
    token: str = Form(...),       # Nu obligatorisk - se require_user()
    map_name: str = Form(None),   # Vad användaren döpte kartan till
    map_id: str = Form(None)      # Om satt, uppdatera en befintlig arkiv-karta istället för att skapa en ny
):
    # Autentisering FÖRST, innan något dyrt (rasterio/lagring) körs - annars kan
    # vem som helst som hittar den publika URL:en förbruka beräkningskraft och
    # lagringsutrymme gratis åt sig själva.
    user_id = require_user(token)
    temp_raw_path = ""
    temp_tiff_path = ""

    try:
        points = json.loads(gcp_data)
        file_extension = image.filename.split(".")[-1].lower()
        unique_id = str(uuid.uuid4())
        unique_filename = f"{unique_id}.{file_extension}"
        geotiff_filename = f"{unique_id}_georeferenced.tif"
        
        temp_raw_path = f"temp_{unique_filename}"
        
        with open(temp_raw_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
            
        print("1. Hanterar Ground Control Points (GCPs)...")
        gcps = []
        for i, p in enumerate(points):
            col, row = p["pixel"][0], p["pixel"][1]
            lat, lng = p["latlng"][0], p["latlng"][1]
            gcps.append(GroundControlPoint(row=row, col=col, x=lng, y=lat, id=str(i)))
            
        src_crs = 'EPSG:4326'
        temp_tiff_path = f"temp_{geotiff_filename}"
        
        print("2. Förbereder gummibandsförvrängning...")
        with rasterio.open(temp_raw_path) as src:
            mem_profile = src.profile.copy()
            mem_profile.update(driver='GTiff', crs=src_crs)
            mem_profile.pop('transform', None)
            # Källbildens egna kodningsinställningar (t.ex. JPEG-kompression med
            # blockysize=1) kraschar GTiff-skrivningen ("RowsPerStrip must be a
            # multiple of 16 for JPEG") om de återanvänds här - de hör bara till
            # källformatet, inte till den okomprimerade mellanlagringsfilen.
            for key in ('compress', 'photometric', 'blockxsize', 'blockysize', 'tiled', 'interleave'):
                mem_profile.pop(key, None)
            
            with MemoryFile() as memfile:
                with memfile.open(**mem_profile, gcps=gcps) as mem_src:
                    mem_src.write(src.read())
                    
                    approx_transform = from_gcps(gcps)
                    left, bottom, right, top = rasterio.transform.array_bounds(src.height, src.width, approx_transform)
                    
                    dst_transform, dst_width, dst_height = calculate_default_transform(
                        src_crs, src_crs, src.width, src.height, left, bottom, right, top
                    )
                    
                    dst_kwargs = mem_src.profile.copy()
                    dst_kwargs.update({
                        'crs': src_crs, 'transform': dst_transform, 'width': dst_width, 'height': dst_height,
                        'compress': 'deflate', 'zlevel': 6, 'tiled': True, 'blockxsize': 256, 'blockysize': 256, 'nodata': 0 
                    })
                    dst_kwargs.pop('gcps', None)

                    print("3. Förvränger (TPS)...")
                    with rasterio.open(temp_tiff_path, 'w', **dst_kwargs) as dst:
                        for i in range(1, mem_src.count + 1):
                            reproject(
                                source=rasterio.band(mem_src, i), destination=rasterio.band(dst, i),
                                dst_transform=dst_transform, dst_crs=src_crs, resampling=Resampling.bilinear, tps=True
                            )
                            
        print("4. Laddar upp GeoTIFF till Supabase Storage...")
        with open(temp_tiff_path, "rb") as tiff_file:
            supabase.storage.from_("geotiffs").upload(
                path=geotiff_filename, file=tiff_file.read(), file_options={"content-type": "image/tiff"}
            )
        tiff_url = supabase.storage.from_("geotiffs").get_public_url(geotiff_filename)

        # NYTT: Spara även originalbilden (oförvrängd) så att kartan kan öppnas
        # igen senare för att lägga till fler punkter och passa in på nytt.
        original_url = None
        with open(temp_raw_path, "rb") as raw_file:
            original_path = f"originals/{unique_filename}"
            supabase.storage.from_("geotiffs").upload(
                path=original_path, file=raw_file.read(), file_options={"content-type": image.content_type or "application/octet-stream"}
            )
            original_url = supabase.storage.from_("geotiffs").get_public_url(original_path)

        print("5. Sparar metadata i databasen...")
        saved_map_id = None
        if user_id:
            map_payload = {
                "name": map_name or image.filename,
                "georeferenced_url": tiff_url,
                "original_image_url": original_url,
                "gcp_json": points
            }
            if map_id:
                # Uppdatera en befintlig karta i arkivet istället för att skapa en ny rad
                db_response = supabase.table("historical_maps").update(map_payload).eq("id", map_id).eq("user_id", user_id).execute()
                if db_response.data:
                    saved_map_id = db_response.data[0]['id']
                    print(f"Uppdaterad i 'Mitt Arkiv'! ID: {saved_map_id}")
            else:
                map_payload["user_id"] = user_id
                db_response = supabase.table("historical_maps").insert(map_payload).execute()
                saved_map_id = db_response.data[0]['id']
                print(f"Sparad i 'Mitt Arkiv'! ID: {saved_map_id}")

        return {
            "status": "success",
            "message": "Kartan georefererad!",
            "geotiff_url": tiff_url,
            "original_image_url": original_url,
            "map_id": saved_map_id
        }
        
    except Exception as e:
        print(f"\n❌ ETT FEL UPPSTOD: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
        
    finally:
        if os.path.exists(temp_raw_path): os.remove(temp_raw_path)
        if os.path.exists(temp_tiff_path): os.remove(temp_tiff_path)

# ==========================================
# 6. LANTMÄTERIET-PROXY (OGC-Features / STAC-bild / STAC-karta)
# ==========================================
# Frontend anropar ALDRIG Lantmäteriet direkt - den pratar bara med oss här,
# och vi bifogar Bearer-token serverside. Så läcker vi aldrig Consumer Key/Secret
# eller ens access-token till webbläsaren.
LANTMATERIET_APIS = {
    "ogc-features": {"base_url": "https://api.lantmateriet.se/ogc-features/v1", "scope": None},
    "stac-bild": {"base_url": "https://api.lantmateriet.se/stac-bild/v1", "scope": None},
    "stac-karta": {"base_url": "https://api.lantmateriet.se/stac-karta/v1", "scope": None},
}

# OGC-Features kräver olika scope beroende på vilken "informationstyp" (första
# path-segmentet) man frågar - t.ex. fastighetsindelning kräver ett annat scope
# än administrativ-indelning. Se swagger.json -> components.securitySchemes.
OGC_FEATURES_SCOPES = {
    "fastighetsindelning": "ogc-features:fastighetsindelning.read",
    "hydrografi": "ogc-features:hydrografi.read",
    "marktacke": "ogc-features:marktacke.read",
    "administrativ-indelning": "ogc-features:ngp.read",
}

@app.get("/api/lantmateriet/{api_name}/{path:path}")
async def lantmateriet_proxy(api_name: str, path: str, request: Request, user_id: str = Depends(require_user_header)):
    config = LANTMATERIET_APIS.get(api_name)
    if not config:
        raise HTTPException(status_code=404, detail=f"Okänt Lantmäteriet-API: {api_name}")
    if not config["base_url"]:
        raise HTTPException(status_code=501, detail=f"base_url för '{api_name}' är inte ifylld i main.py ännu")

    scope = config["scope"]
    if api_name == "ogc-features":
        informationstyp = path.split("/")[0] if path else ""
        scope = OGC_FEATURES_SCOPES.get(informationstyp, "ogc-features:ngp.read")

    token = await get_lantmateriet_token(scope)
    url = f"{config['base_url'].rstrip('/')}/{path}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=dict(request.query_params), headers={"Authorization": f"Bearer {token}"})

    return Response(content=resp.content, media_type=resp.headers.get("content-type"), status_code=resp.status_code)

# ==========================================
# 7. LANTMÄTERIET FIL-PROXY (streaming, för stora COG-filer t.ex. ortofoton)
# ==========================================
# STAC-katalogens "assets.data.href" pekar på riktiga bildfiler (kan vara 100-tals MB)
# på ett ANNAT värdnamn (dl*.lantmateriet.se) än katalog-API:et, och kräver samma
# Bearer-token. Eftersom georaster/geotiff.js läser COG-filer via HTTP Range-requests
# (bara de delar som faktiskt visas, inte hela filen), måste vår proxy vidarebefordra
# Range-headern och strömma svaret rakt igenom - INTE buffra hela filen i minnet.
ALLOWED_LANTMATERIET_FILE_SUFFIX = ".lantmateriet.se"

@app.api_route("/api/lantmateriet-file", methods=["GET", "HEAD"])
async def lantmateriet_file_proxy(request: Request, url: str, supabase_token: str, range: str = Header(None)):
    # supabase_token kommer som query-parameter (inte header) eftersom denna URL
    # skickas rakt in i parseGeoraster()/geotiff.js, som gör sina egna interna
    # range-request-anrop utan att vi kan bifoga en Authorization-header.
    require_user(supabase_token)

    parsed = urlparse(url)
    if not parsed.hostname or not parsed.hostname.endswith(ALLOWED_LANTMATERIET_FILE_SUFFIX) or parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Otillåten värd - endast *.lantmateriet.se över https tillåts")

    token = await get_lantmateriet_token(None)
    headers = {"Authorization": f"Bearer {token}"}
    if range:
        headers["Range"] = range

    if request.method == "HEAD":
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.head(url, headers=headers)
        passthrough_headers = {h: resp.headers[h] for h in ("content-type", "content-length", "accept-ranges", "etag", "last-modified") if h in resp.headers}
        return Response(status_code=resp.status_code, headers=passthrough_headers)

    client = httpx.AsyncClient(timeout=60.0)
    req = client.build_request("GET", url, headers=headers)
    resp = await client.send(req, stream=True)

    async def body():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    passthrough_headers = {}
    for h in ("content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified", "cache-control"):
        if h in resp.headers:
            passthrough_headers[h] = resp.headers[h]

    return StreamingResponse(body(), status_code=resp.status_code, headers=passthrough_headers)