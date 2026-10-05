import os
import json
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException


# ============================================================
# CONFIG
# ============================================================

API_URL = (
    "https://live.ipms247.com/"
    "booking/reservation_api/listing.php"
)

HOTEL_CODE = os.getenv(
    "IPMS_HOTEL_CODE",
    "23400"
).strip()

OPENAPI_KEY = os.getenv(
    "IPMS_OPENAPI_KEY",
    ""
).strip()


# ============================================================
# ROOM ORDER
# ============================================================

ROOM_ORDER = [
    "The Camper room",
    "The Glamper room",
    "The Surveyor room",
    "The Surveyor suite room",
    "The Zenith luxury cottage",
    "The Twin luxury cottage",
    "The Villa",
]


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="EBC Room Availability API",
    version="1.0.0",
)


# ============================================================
# HELPERS
# ============================================================

def safe_float(value: Any) -> float:
    if value is None:
        return 0.0

    try:
        text = str(value).strip()

        if not text:
            return 0.0

        text = (
            text
            .replace(",", "")
            .replace("₹", "")
            .replace("Rs.", "")
            .replace("Rs", "")
            .strip()
        )

        match = re.search(
            r"-?\d+(?:\.\d+)?",
            text
        )

        if not match:
            return 0.0

        return float(match.group(0))

    except (ValueError, TypeError):
        return 0.0


def safe_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        if isinstance(value, bool):
            return int(value)

        text = str(value).strip()

        if not text:
            return 0

        return int(
            float(
                text.replace(",", "")
            )
        )

    except (ValueError, TypeError):
        return 0


def round_price_down_100(value: Any) -> int:
    """
    Examples:

    17680.635 -> 17600
    11530.64  -> 11500
    12712     -> 12700
    13418     -> 13400
    """

    price = safe_float(value)

    if price <= 0:
        return 0

    return int(
        math.floor(price / 100) * 100
    )


# ============================================================
# ROOM NAME
# ============================================================

def normalize_room_name(
    room_name: Any,
    room_type: Any = "",
) -> Optional[str]:

    name = str(
        room_name or ""
    ).lower()

    room_type_text = str(
        room_type or ""
    ).lower()

    combined = (
        f"{name} {room_type_text}"
    )

    if "camper" in combined:
        return "The Camper room"

    if "glamper" in combined:
        return "The Glamper room"

    if (
        "surveyor" in combined
        and "suite" in combined
    ):
        return "The Surveyor suite room"

    if "surveyor" in combined:
        return "The Surveyor room"

    if "zenith" in combined:
        return "The Zenith luxury cottage"

    if (
        "twin" in combined
        and "luxury" in combined
    ):
        return "The Twin luxury cottage"

    if "villa" in combined:
        return "The Villa"

    return None


def extract_room_name(
    record: Dict[str, Any]
) -> Optional[str]:

    room_name = record.get(
        "Room_Name"
    )

    room_type = record.get(
        "Roomtype_Name"
    )

    result = normalize_room_name(
        room_name,
        room_type,
    )

    if result:
        return result

    # Fallback
    return normalize_room_name(
        record.get(
            "Room_Description"
        ),
        room_type,
    )


# ============================================================
# AVAILABLE ROOMS
# ============================================================

def extract_available_rooms(
    record: Dict[str, Any],
    check_in: str,
) -> int:

    # ========================================================
    # ACTUAL IPMS STRUCTURE
    #
    # "available_rooms": {
    #     "2026-10-05": "1"
    # }
    # ========================================================

    available_rooms = record.get(
        "available_rooms"
    )

    if isinstance(
        available_rooms,
        dict
    ):

        # Exact check-in date
        if check_in in available_rooms:

            count = safe_int(
                available_rooms.get(
                    check_in
                )
            )

            if count > 0:
                return count

        # Fallback: first positive date
        for value in available_rooms.values():

            count = safe_int(value)

            if count > 0:
                return count

    else:

        count = safe_int(
            available_rooms
        )

        if count > 0:
            return count

    # ========================================================
    # Fallback: min_ava_rooms
    # ========================================================

    count = safe_int(
        record.get(
            "min_ava_rooms"
        )
    )

    if count > 0:
        return count

    # ========================================================
    # Other possible fields
    # ========================================================

    for field in [
        "availableRooms",
        "availableRoom",
        "roomsAvailable",
        "availability",
        "available",
        "Available_Rooms",
        "Available",
    ]:

        value = record.get(field)

        if isinstance(
            value,
            dict
        ):

            if check_in in value:

                count = safe_int(
                    value.get(
                        check_in
                    )
                )

                if count > 0:
                    return count

        else:

            count = safe_int(value)

            if count > 0:
                return count

    return 0


# ============================================================
# PRICE
# ============================================================

def extract_nightly_price(
    record: Dict[str, Any],
    check_in: str,
) -> float:

    # ========================================================
    # ACTUAL IPMS247 STRUCTURE
    #
    # room_rates_info:
    #
    # {
    #   "avg_per_night_without_tax": 17680.635,
    #   "avg_per_night_after_discount": 17680.64,
    #   "exclusive_tax": {
    #       "2026-10-05": 17680.635
    #   },
    #   ...
    # }
    # ========================================================

    rates = record.get(
        "room_rates_info"
    )

    if isinstance(
        rates,
        str
    ):

        try:
            rates = json.loads(
                rates
            )

        except (
            ValueError,
            TypeError
        ):
            rates = None

    if isinstance(
        rates,
        dict
    ):

        # ----------------------------------------------------
        # BEST CUSTOMER-FACING VALUE
        #
        # This is the average discounted price per night
        # BEFORE TAX.
        # ----------------------------------------------------

        value = rates.get(
            "avg_per_night_without_tax"
        )

        price = safe_float(value)

        if price > 0:
            return price

        # ----------------------------------------------------
        # Fallback:
        # avg_per_night_after_discount
        # ----------------------------------------------------

        value = rates.get(
            "avg_per_night_after_discount"
        )

        price = safe_float(value)

        if price > 0:

            # This field can be inclusive of tax,
            # so prefer exclusive_tax below if available.
            exclusive = rates.get(
                "exclusive_tax"
            )

            if isinstance(
                exclusive,
                dict
            ):

                date_value = exclusive.get(
                    check_in
                )

                exclusive_price = safe_float(
                    date_value
                )

                if exclusive_price > 0:
                    return exclusive_price

            return price

        # ----------------------------------------------------
        # exclusive_tax[check_in]
        # ----------------------------------------------------

        exclusive = rates.get(
            "exclusive_tax"
        )

        if isinstance(
            exclusive,
            dict
        ):

            price = safe_float(
                exclusive.get(
                    check_in
                )
            )

            if price > 0:
                return price

        # ----------------------------------------------------
        # exclusivetax_baserate[check_in]
        # ----------------------------------------------------

        exclusive_base = rates.get(
            "exclusivetax_baserate"
        )

        if isinstance(
            exclusive_base,
            dict
        ):

            price = safe_float(
                exclusive_base.get(
                    check_in
                )
            )

            if price > 0:
                return price

        # ----------------------------------------------------
        # Day-wise base rate
        # ----------------------------------------------------

        day_wise = rates.get(
            "day_wise_baserackrate"
        )

        if isinstance(
            day_wise,
            list
        ):

            for value in day_wise:

                price = safe_float(
                    value
                )

                if price > 0:
                    return price

        # ----------------------------------------------------
        # Total price room only
        # ----------------------------------------------------

        price = safe_float(
            rates.get(
                "totalprice_room_only"
            )
        )

        if price > 0:
            return price

    # ========================================================
    # OLD / ALTERNATIVE IPMS STRUCTURE
    # ========================================================

    price_per_night = record.get(
        "pricePerNight"
    )

    if isinstance(
        price_per_night,
        dict
    ):

        day = price_per_night.get(
            check_in
        )

        if isinstance(
            day,
            dict
        ):

            discounted = safe_float(
                day.get(
                    "discountedPrice"
                )
            )

            if discounted > 0:
                return discounted

            regular = safe_float(
                day.get(
                    "regularPrice"
                )
            )

            if regular > 0:
                return regular

    # ========================================================
    # Other fallback fields
    # ========================================================

    for field in [
        "discountedPrice",
        "DiscountedPrice",
        "discounted_price",
        "regularPrice",
        "RegularPrice",
        "price",
        "Price",
        "roomPrice",
        "RoomPrice",
        "rate",
        "Rate",
    ]:

        price = safe_float(
            record.get(field)
        )

        if price > 0:
            return price

    return 0.0


# ============================================================
# IPMS247 REQUEST
# ============================================================

def fetch_ipms_availability(
    check_in: str,
    check_out: str,
    adults: int,
    children: int,
    rooms: int,
) -> Any:

    if not OPENAPI_KEY:

        raise RuntimeError(
            "IPMS247 API key is not configured."
        )

    # --------------------------------------------------------
    # Calculate nights
    # --------------------------------------------------------

    try:

        start = datetime.strptime(
            check_in,
            "%Y-%m-%d"
        ).date()

        end = datetime.strptime(
            check_out,
            "%Y-%m-%d"
        ).date()

    except ValueError as exc:

        raise ValueError(
            "Invalid date format. "
            "Use YYYY-MM-DD."
        ) from exc

    nights = (
        end - start
    ).days

    if nights <= 0:

        raise ValueError(
            "Check-out date must be "
            "after check-in date."
        )

    # --------------------------------------------------------
    # IPMS247 parameters
    #
    # IMPORTANT:
    # Do NOT send check_out_date.
    # Use check_in_date + num_nights.
    # --------------------------------------------------------

    params = {
        "request_type": "RoomList",
        "HotelCode": HOTEL_CODE,
        "APIKey": OPENAPI_KEY,

        "check_in_date": check_in,
        "num_nights": nights,

        "number_adults": adults,
        "number_children": children,
        "num_rooms": rooms,

        "property_configuration_info": 0,
        "showtax": 0,

        "show_only_available_rooms": 1,
        "show_matched_minimum_nights_rateplans": 1,

        "language": "en",

        "packagefor": "DESKTOP",
        "promotionfor": "DESKTOP",
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/155.0.0.0 "
            "Safari/537.36"
        ),

        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),

        "Accept-Language": (
            "en-US,en;q=0.9"
        ),

        "Referer": (
            "https://letsbook.me/"
        ),

        "Origin": (
            "https://letsbook.me"
        ),
    }

    try:

        response = requests.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=60,
            allow_redirects=True,
        )

    except requests.RequestException as exc:

        print(
            "IPMS_REQUEST_ERROR | "
            f"{type(exc).__name__}: {exc}"
        )

        raise RuntimeError(
            "Unable to connect to IPMS247."
        ) from exc

    # --------------------------------------------------------
    # Response debug
    #
    # API KEY is NOT printed.
    # --------------------------------------------------------

    body = response.text or ""

    print(
        "IPMS_STATUS | "
        f"{response.status_code}"
    )

    print(
        "IPMS_CONTENT_TYPE | "
        f"{response.headers.get('content-type')}"
    )

    print(
        "IPMS_BODY | "
        f"{body[:3000].replace(chr(10), ' ')}"
    )

    # --------------------------------------------------------
    # HTTP status
    # --------------------------------------------------------

    if response.status_code != 200:

        raise RuntimeError(
            "IPMS247 API request failed with "
            f"HTTP {response.status_code}."
        )

    # --------------------------------------------------------
    # Security response
    # --------------------------------------------------------

    lower_body = body.lower()

    if any(
        marker in lower_body
        for marker in [
            "javascript is required",
            "sucuri",
            "access denied",
            "request blocked",
            "captcha",
            "security verification",
        ]
    ):

        raise RuntimeError(
            "IPMS247 security protection "
            "blocked the Vercel server request."
        )

    # --------------------------------------------------------
    # Empty body
    # --------------------------------------------------------

    if not body.strip():

        return {
            "status": "no_availability",
            "data": [],
        }

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    try:

        parsed = response.json()

    except ValueError as exc:

        raise RuntimeError(
            "IPMS247 returned a non-JSON response."
        ) from exc

    # --------------------------------------------------------
    # Handle explicit no data
    # --------------------------------------------------------

    if (
        isinstance(
            parsed,
            dict
        )
        and str(
            parsed.get(
                "status",
                ""
            )
        ).lower()
        in {
            "error",
            "failed",
            "failure",
        }
    ):

        message = (
            parsed.get("message")
            or parsed.get("error")
            or parsed.get(
                "errorMessage"
            )
            or "Unknown IPMS247 error."
        )

        raise RuntimeError(
            f"IPMS247 error: {message}"
        )

    if (
        "no data found"
        in lower_body
    ):

        return {
            "status": "no_availability",
            "data": [],
        }

    return parsed


# ============================================================
# PROCESS ROOMS
# ============================================================

def process_rooms(
    raw_response: Any,
    check_in: str,
) -> List[Dict[str, Any]]:

    # --------------------------------------------------------
    # IPMS can return:
    #
    # {"status":"success","data":[...]}
    #
    # OR:
    #
    # [...]
    # --------------------------------------------------------

    if isinstance(
        raw_response,
        dict
    ):

        data = raw_response.get(
            "data"
        )

        if data is None:

            status = str(
                raw_response.get(
                    "status",
                    ""
                )
            ).lower()

            if status in {
                "no_availability",
                "nodata",
                "no_data",
            }:

                return []

            raise RuntimeError(
                "IPMS247 response did not "
                "contain room data."
            )

    elif isinstance(
        raw_response,
        list
    ):

        data = raw_response

    else:

        raise RuntimeError(
            "Unexpected IPMS247 response format."
        )

    if not data:
        return []

    if not isinstance(
        data,
        list
    ):

        raise RuntimeError(
            "IPMS247 room data is not a list."
        )

    grouped: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # --------------------------------------------------------
    # Process every room
    # --------------------------------------------------------

    for record in data:

        if not isinstance(
            record,
            dict
        ):
            continue

        # ----------------------------------------------------
        # ROOM NAME
        # ----------------------------------------------------

        room_name = extract_room_name(
            record
        )

        if not room_name:

            print(
                "ROOM_SKIP | "
                "reason=unknown_room | "
                f"Room_Name="
                f"{record.get('Room_Name')}"
            )

            continue

        # ----------------------------------------------------
        # AVAILABILITY
        # ----------------------------------------------------

        available = extract_available_rooms(
            record,
            check_in,
        )

        if available <= 0:

            print(
                "ROOM_SKIP | "
                "reason=not_available | "
                f"room={room_name}"
            )

            continue

        # ----------------------------------------------------
        # PRICE
        # ----------------------------------------------------

        raw_price = extract_nightly_price(
            record,
            check_in,
        )

        if raw_price <= 0:

            print(
                "ROOM_SKIP | "
                "reason=no_price | "
                f"room={room_name}"
            )

            continue

        # ----------------------------------------------------
        # Round down to nearest ₹100
        # ----------------------------------------------------

        display_price = (
            round_price_down_100(
                raw_price
            )
        )

        room_data = {
            "name": room_name,
            "available": available,
            "price": display_price,
            "_raw_price": raw_price,
        }

        # ----------------------------------------------------
        # Duplicate room types
        # Keep cheapest price.
        # ----------------------------------------------------

        existing = grouped.get(
            room_name
        )

        if existing is None:

            grouped[
                room_name
            ] = room_data

        else:

            if raw_price < existing[
                "_raw_price"
            ]:

                grouped[
                    room_name
                ] = room_data

            elif (
                available
                > existing["available"]
            ):

                existing[
                    "available"
                ] = available

    # --------------------------------------------------------
    # Final list
    # --------------------------------------------------------

    result = list(
        grouped.values()
    )

    for room in result:

        room.pop(
            "_raw_price",
            None
        )

    # --------------------------------------------------------
    # Sort
    # --------------------------------------------------------

    result.sort(
        key=lambda item: (
            ROOM_ORDER.index(
                item["name"]
            )
            if item["name"]
            in ROOM_ORDER
            else 999
        )
    )

    print(
        "ROOM_RESULT | "
        f"{json.dumps(result, ensure_ascii=False)}"
    )

    # --------------------------------------------------------
    # Don't incorrectly say "no rooms"
    # if IPMS returned room records but parsing failed.
    # --------------------------------------------------------

    if (
        len(data) > 0
        and len(result) == 0
    ):

        sample = []

        for record in data[:2]:

            if isinstance(
                record,
                dict
            ):

                sample.append({
                    "Room_Name": record.get(
                        "Room_Name"
                    ),
                    "Roomtype_Name": record.get(
                        "Roomtype_Name"
                    ),
                    "available_rooms": record.get(
                        "available_rooms"
                    ),
                    "room_rates_info": record.get(
                        "room_rates_info"
                    ),
                })

        raise RuntimeError(
            "IPMS247 returned room data, "
            "but the room availability or "
            "price could not be recognized. "
            f"Sample: "
            f"{json.dumps(sample, ensure_ascii=False)[:3000]}"
        )

    return result


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "status": "ok",
        "service": (
            "EBC Room Availability API"
        ),
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
def health():

    return {
        "status": "healthy"
    }


# ============================================================
# AVAILABILITY
# ============================================================

@app.get("/api/availability")
def availability(
    check_in: str,
    check_out: str,
    adults: int = 2,
    children: int = 0,
    rooms: int = 1,
):

    # --------------------------------------------------------
    # Validate dates
    # --------------------------------------------------------

    try:

        start = datetime.strptime(
            check_in,
            "%Y-%m-%d"
        ).date()

        end = datetime.strptime(
            check_out,
            "%Y-%m-%d"
        ).date()

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid date format. "
                "Use YYYY-MM-DD."
            ),
        )

    if end <= start:

        raise HTTPException(
            status_code=400,
            detail=(
                "Check-out date must be "
                "after check-in date."
            ),
        )

    # --------------------------------------------------------
    # Validate inputs
    # --------------------------------------------------------

    if adults < 1:

        raise HTTPException(
            status_code=400,
            detail=(
                "Adults must be at least 1."
            ),
        )

    if children < 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "Children cannot be negative."
            ),
        )

    if rooms < 1:

        raise HTTPException(
            status_code=400,
            detail=(
                "Rooms must be at least 1."
            ),
        )

    # --------------------------------------------------------
    # Fetch IPMS
    # --------------------------------------------------------

    try:

        raw_response = (
            fetch_ipms_availability(
                check_in=check_in,
                check_out=check_out,
                adults=adults,
                children=children,
                rooms=rooms,
            )
        )

        # ----------------------------------------------------
        # Explicit no availability
        # ----------------------------------------------------

        if (
            isinstance(
                raw_response,
                dict
            )
            and raw_response.get(
                "status"
            )
            == "no_availability"
        ):

            return {
                "success": True,
                "available": False,
                "check_in": check_in,
                "check_out": check_out,
                "rooms": [],
            }

        # ----------------------------------------------------
        # Process rooms
        # ----------------------------------------------------

        rooms_data = process_rooms(
            raw_response,
            check_in,
        )

        return {
            "success": True,
            "available": bool(
                rooms_data
            ),
            "check_in": check_in,
            "check_out": check_out,
            "rooms": rooms_data,
        }

    except HTTPException:
        raise

    except Exception as exc:

        print(
            "AVAILABILITY_ERROR | "
            f"{type(exc).__name__}: {exc}"
        )

        raise HTTPException(
            status_code=502,
            detail=str(exc)
        )