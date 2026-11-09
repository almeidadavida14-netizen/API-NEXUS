"""Consulta de perfil Free Fire via API pública FWX.

A consulta de perfil é separada do cadastro persistente: nada é salvo ao consultar.
O jogador só entra na lista se clicar explicitamente no botão de adicionar.
"""
import asyncio
import json
import sqlite3
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://freefirefwx-beta.squareweb.app"


class ApiAccessDenied(RuntimeError):
    """A hospedagem da API recusou a requisição antes de processar o UID."""

DB_PATH = Path(__file__).resolve().parent / "data" / "freefire_players.sqlite3"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _get_json(path, params):
    url = f"{BASE_URL}{path}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "EVOM-NEXUS/1.1 (+https://example.org)", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
            if response.status != 200:
                raise RuntimeError(f"API respondeu HTTP {response.status}")
            data = json.loads(body)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code == 403:
            raise ApiAccessDenied(
                "A hospedagem da API Free Fire FWX bloqueou a requisição (HTTP 403). "
                "Isso não confirma erro no UID nem na região. Tente novamente mais tarde "
                "ou peça ao responsável pela API para liberar o acesso ao endpoint."
            ) from exc
        raise RuntimeError(f"API respondeu HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Não foi possível consultar a API Free Fire: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("A API retornou um formato inesperado.")
    return data


def _region_from(data):
    """Handle the response variants returned by the region and nickname endpoints."""
    candidates = [data]
    # Some deployments wrap the player object more than one level deep.
    index = 0
    while index < len(candidates) and len(candidates) < 20:
        obj = candidates[index]
        index += 1
        for value in obj.values():
            if isinstance(value, dict) and value not in candidates:
                candidates.append(value)
    for obj in candidates:
        for key in ("region", "player_region", "PlayerRegion", "accountRegion", "AccountRegion"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                region = value.strip().lower()
                if region in {"br", "sac", "us", "na", "latam", "sg", "eu", "me", "vn", "tw", "pk", "ru", "bd", "id", "th", "ind"}:
                    return region
    return None


async def fetch_profile(uid: str):
    uid = str(uid).strip()
    if not uid.isdigit() or not (5 <= len(uid) <= 20):
        raise ValueError("Informe um UID numérico válido do Free Fire.")

    # A API pode responder de forma diferente entre /region e /nick. Guardamos
    # os erros para mostrar uma causa útil e, se ambos falharem, tentamos as
    # regiões públicas documentadas pelo próprio serviço.
    lookup_errors = []
    region = None
    for path, params in (("/api/region", {"id": uid}), ("/api/nick", {"id": uid})):
        try:
            lookup = await asyncio.to_thread(_get_json, path, params)
            region = _region_from(lookup)
            if region:
                break
            lookup_errors.append(f"{path}: resposta sem região reconhecida")
        except ApiAccessDenied as exc:
            # Não repetir cinco consultas quando a própria hospedagem está bloqueando.
            raise RuntimeError(str(exc)) from exc
        except Exception as exc:
            lookup_errors.append(f"{path}: {exc}")

    # Prefer the detected region. When lookup endpoints are unavailable or
    # malformed, try the documented regions with info_player as a fallback.
    regions_to_try = ([region] if region else []) + [r for r in ("br", "sac", "latam", "us", "na") if r != region]
    last_profile_error = None
    for candidate_region in regions_to_try:
        try:
            data = await asyncio.to_thread(
                _get_json, "/api/info_player",
                {"uid": uid, "region": candidate_region, "clothes": "true"},
            )
        except ApiAccessDenied as exc:
            raise RuntimeError(str(exc)) from exc
        except Exception as exc:
            last_profile_error = exc
            continue
        if data.get("error") or data.get("success") is False or data.get("status") == "error":
            last_profile_error = RuntimeError(str(data.get("message") or data.get("error") or "Perfil não encontrado pela API."))
            continue
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        basic = data.get("basicInfo") or payload.get("basicInfo") or data.get("AccountInfo") or payload.get("AccountInfo") or {}
        profile = data.get("profileInfo") or payload.get("profileInfo") or data.get("AccountProfileInfo") or payload.get("AccountProfileInfo") or {}
        clan = data.get("clanBasicInfo") or payload.get("clanBasicInfo") or data.get("GuildInfo") or payload.get("GuildInfo") or {}
        if not isinstance(basic, dict) or not basic:
            last_profile_error = RuntimeError(str(data.get("message") or data.get("error") or "Resposta sem dados básicos do jogador."))
            continue
        nickname = basic.get("nickname") or basic.get("AccountName") or basic.get("player_nickname")
        if not nickname:
            last_profile_error = RuntimeError("A API retornou o perfil sem nickname.")
            continue
        clothes = profile.get("clothes") or {}
        images = clothes.get("images") or [] if isinstance(clothes, dict) else []
        clothes_url = next((x for x in images if isinstance(x, str) and x.startswith("https://")), None)
        avatar = basic.get("avatars", {}) if isinstance(basic.get("avatars"), dict) else {}
        avatar_url = avatar.get("png") or avatar.get("webp") or basic.get("avatarUrl") or basic.get("headPicUrl")
        level = basic.get("level", basic.get("AccountLevel", "—"))
        resolved_region = _region_from({"region": basic.get("region") or basic.get("AccountRegion")}) or candidate_region
        return {"uid": uid, "region": resolved_region, "nickname": nickname, "level": level,
                "avatar": avatar_url, "clothes": clothes_url,
                "clan": clan.get("clanName") or clan.get("clan_name") or clan.get("GuildName"), "basic": basic}

    if region is None:
        detail = "; ".join(lookup_errors[-2:])
        raise RuntimeError("Não consegui descobrir a região do UID. A API /region e /nick não retornaram uma região e a busca nas regiões suportadas também falhou. " + detail)
    raise RuntimeError(f"A região {region.upper()} foi identificada, mas a API não retornou o perfil para esse UID. Verifique o UID ou tente novamente mais tarde. Detalhe: {last_profile_error or 'perfil não encontrado'}")


def save_player(uid, nickname, guild_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS saved_players (guild_id INTEGER NOT NULL, uid TEXT NOT NULL, nickname TEXT NOT NULL, PRIMARY KEY(guild_id, uid))")
        db.execute("INSERT OR REPLACE INTO saved_players(guild_id, uid, nickname) VALUES(?,?,?)", (guild_id, uid, nickname))


def list_players(guild_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS saved_players (guild_id INTEGER NOT NULL, uid TEXT NOT NULL, nickname TEXT NOT NULL, PRIMARY KEY(guild_id, uid))")
        return db.execute("SELECT nickname, uid FROM saved_players WHERE guild_id=? ORDER BY nickname COLLATE NOCASE", (guild_id,)).fetchall()


async def fetch_stats(uid: str, region: str, mode: str = "cs"):
    """Busca estatísticas públicas; retorna None se a API não disponibilizar dados."""
    mode = str(mode).strip().lower()
    if mode not in {"cs", "br"}:
        raise ValueError("O modo deve ser 'cs' ou 'br'.")
    data = await asyncio.to_thread(_get_json, "/api/stats", {"id": str(uid), "region": str(region).lower(), "mode": mode})
    if data.get("success") is False or data.get("status") == "error" or data.get("error"):
        raise RuntimeError(str(data.get("message") or data.get("error") or "Estatísticas indisponíveis."))
    results = data.get("results")
    if not isinstance(results, dict):
        return {}
    prefix = "CS" if mode == "cs" else "BR"
    # Prioriza ranked; usa career/classic como fallback conforme os dados retornados.
    for suffix in ("RANKED", "CAREER", "CLASSIC"):
        block = results.get(f"{prefix}_{suffix}")
        if isinstance(block, dict) and block:
            return block
    return {}
