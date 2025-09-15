
# app.py
# -----------------------------------------------------------------------------
# Piercing Shop Inventory & Wishlist (Streamlit Prototype)
#
# How to run:
#   streamlit run app.py
#
# This prototype loads product data from `products.json` (same folder).
# It demonstrates:
#   - A placeholder Login page
#   - A Main gallery with tag filtering
#   - A Product page with dynamic variant selection and variant image preview
#   - A Wishlist page storing chosen variant combinations per user session
#
# ❗ IMPORTANT – Streamlit prototype vs. production web apps:
# -----------------------------------------------------------------------------
# • Authentication:
#   - This app shows a *placeholder* login screen. It does NOT authenticate users.
#   - Streamlit does not provide a built-in, secure, multi-user auth/session system.
#   - In production, implement authentication with a proper backend (Django/Flask/FastAPI),
#     using secure password hashing, server-side sessions (or JWT), CSRF protection,
#     password resets, SSO/OAuth/OpenID Connect, and role-based access control.
#
# • Persistence (wishlist, users, products):
#   - This prototype saves the wishlist only in `st.session_state` (in-memory per browser
#     session). It disappears on refresh or on a new device.
#   - In production, persist user-specific wishlists to a database (e.g., Postgres/MySQL)
#     keyed to the authenticated user ID. Use an ORM (Django ORM / SQLAlchemy) and
#     transactions, with proper schema for Products, Variants, and Wishlists.
#
# • Concurrency / multi-user:
#   - Streamlit sessions are isolated per browser connection. There's no concept of
#     shared user records or transactional writes in this file alone.
#   - With Django/Flask, implement endpoints and database transactions to support
#     multiple users simultaneously with data integrity (unique constraints,
#     foreign keys, optimistic locking where needed).
#
# • Business logic (variants, availability, pricing):
#   - Here we infer/normalize variants from the provided JSON for UI dropdowns.
#   - In production, you would model Products, VariantAxes (e.g., Length, Color),
#     VariantOptions, and Variant SKUs explicitly. Also attach price/stock to
#     concrete combinations. Validate that chosen combinations exist before adding
#     to a wishlist or cart. Store media in a CDN/S3 and serve signed URLs.
#
# • Routing:
#   - Streamlit is single-file with basic “pseudo-routing” via `st.session_state['page']`.
#   - In production frameworks, use real routes (e.g., /login, /products, /product/<sku>,
#     /wishlist) with templates or component-based UIs, plus server-side redirects.
# -----------------------------------------------------------------------------

import json
import os
import re
from typing import Dict, List, Any, Optional, Tuple

import streamlit as st

# ------------------------------ Utilities & Data ------------------------------


@st.cache_data(show_spinner=False)
def load_products(path: str = "products.json") -> Dict[str, Any]:
    """Load and return the entire products JSON.

    In Streamlit, @st.cache_data caches results for the current script state.
    In production, you'd likely read from a database or an API layer with
    proper error handling, retries, and observability (logging/metrics).
    """
    if not os.path.exists(path):
        st.error(
            f"Could not find `{path}`. Make sure it exists next to app.py "
            f"and contains the provided product JSON."
        )
        st.stop()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def best_image(product: Dict[str, Any]) -> Optional[str]:
    """Prefer local image path, fall back to remote URL."""
    img_local = product.get("main_image_local")
    img_remote = product.get("main_image")
    return img_local or img_remote


def get_all_products(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return data.get("products", [])


def all_unique_tags(products: List[Dict[str, Any]]) -> List[str]:
    tags = set()
    for p in products:
        for t in p.get("tags", []) or []:
            if t and isinstance(t, str):
                tags.add(t.strip())
    return sorted(tags)


def filter_by_tags(products: List[Dict[str, Any]], selected: List[str]) -> List[Dict[str, Any]]:
    if not selected:
        return products
    sel = set(selected)
    out = []
    for p in products:
        p_tags = set([t.strip() for t in (p.get("tags") or [])])
        if sel.issubset(p_tags):
            out.append(p)
    return out


_MM_RE = re.compile(r"^\s*\d+(?:\.\d+)?\s*mm\s*$", re.IGNORECASE)


def normalize_axis_label(raw_label: str, option_names: List[str]) -> str:
    """Normalize axis labels (e.g., CZ Color/Crystal Color → Color).
    If all options look like 'xx mm', label becomes Length/Size.
    """
    label = (raw_label or "").strip()
    # If all options look like sizes in mm, prefer Length/Size naming.
    if option_names and all(_MM_RE.match(str(x or "")) for x in option_names):
        # Choose 'Length' by default for mm values.
        return "Length"

    lower = label.lower()
    if lower in {"cz color", "crystal color", "color"}:
        return "Color"
    if lower in {"packing option", "packing", "package"}:
        return "Packing Option"
    if lower in {"rack"}:
        return "Rack"
    if lower in {"gauge"}:
        return "Gauge"
    if lower in {"size"}:
        return "Size"
    return label or "Option"


_PRICEY_COLS = {"price", "price / pc", "price/pc", "price per pc", "price per pair", "price per piece"}


def _clean_option_name(name: Any) -> str:
    s = ("" if name is None else str(name)).strip()
    return s if s else "Unspecified"


def build_variant_axes(product: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Construct UI-friendly axes from product['variants'].

    Returns a list of axes dicts:
      {
        "label": "Length",
        "options": ["8.00mm", "10.00mm"],
        "image_map": {"Clear": "path/to/img.jpg", ...},
        "source_blocks": [<indices>],  # which variant blocks contributed
        "kind": "merged" | "standard" | "color",
      }

    We merge axes with the same normalized label across blocks to avoid duplicates
    (e.g., 'Crystal Color' table + 'CZ Color' swatch block).
    """
    axes_by_label: Dict[str, Dict[str, Any]] = {}

    blocks = product.get("variants") or []
    for idx, v in enumerate(blocks):
        vtype = v.get("type")
        if vtype == "standard_variant":
            headers = v.get("headers") or []
            items = v.get("items") or []
            # For each header, if not a price-like column, collect options and any per-row image mapping.
            for h in headers:
                if (h or "").strip().lower() in _PRICEY_COLS:
                    continue
                options_order: List[str] = []
                image_map: Dict[str, str] = {}
                for row in items:
                    val = _clean_option_name(row.get(h))
                    if val and val not in options_order:
                        options_order.append(val)
                    # Some rows carry images for a specific column (e.g., Crystal Color).
                    img_local = row.get("image_local")
                    img = row.get("image")
                    if img_local:
                        image_map[val] = img_local
                    elif img:
                        image_map[val] = img
                normalized = normalize_axis_label(h, options_order)
                bucket = axes_by_label.setdefault(
                    normalized,
                    {"label": normalized, "options": [], "image_map": {}, "source_blocks": [], "kind": "merged"},
                )
                # Merge while preserving order
                for opt in options_order:
                    if opt not in bucket["options"]:
                        bucket["options"].append(opt)
                bucket["image_map"].update(image_map)
                bucket["source_blocks"].append(idx)

        elif vtype == "color_variant":
            label = v.get("label") or "Color"
            options = v.get("options") or []
            opt_names: List[str] = []
            image_map: Dict[str, str] = {}
            for opt in options:
                name = _clean_option_name(opt.get("name"))
                opt_names.append(name)
                img_local = opt.get("image_local")
                img = opt.get("image")
                if img_local:
                    image_map[name] = img_local
                elif img:
                    image_map[name] = img
            normalized = normalize_axis_label(label, opt_names)
            bucket = axes_by_label.setdefault(
                normalized,
                {"label": normalized, "options": [], "image_map": {}, "source_blocks": [], "kind": "merged"},
            )
            for name in opt_names:
                if name not in bucket["options"]:
                    bucket["options"].append(name)
            bucket["image_map"].update(image_map)
            bucket["source_blocks"].append(idx)

        # Ignore unknown block types silently for robustness.

    # Stable order: prefer common axes first
    priority = {"Length": 0, "Size": 1, "Gauge": 2, "Color": 3, "Packing Option": 4, "Rack": 5}
    return sorted(axes_by_label.values(), key=lambda ax: (priority.get(ax["label"], 99), ax["label"]))


def compute_price_info(product: Dict[str, Any], selections: Dict[str, str]) -> Dict[str, Any]:
    """Try to find price info in standard_variant rows that match current selections.

    We match within each *standard_variant* block by comparing the selection for any of its
    non-price headers. If a row matches, we extract 'Price' and 'Price / pc'.
    This is best-effort; the provided JSON doesn't always encode full combinations.
    """
    price_info: Dict[str, Any] = {}
    for v in product.get("variants") or []:
        if v.get("type") != "standard_variant":
            continue
        headers = v.get("headers") or []
        match_headers = [h for h in headers if (h or "").strip().lower() not in _PRICEY_COLS]
        if not match_headers:
            continue
        for row in v.get("items") or []:
            # For a match, all match_headers must equal the selection (if the selection exists)
            ok = True
            for h in match_headers:
                norm_label = normalize_axis_label(h, [])
                sel_val = selections.get(norm_label)
                if sel_val is None:
                    continue
                if _clean_option_name(row.get(h)) != sel_val:
                    ok = False
                    break
            if ok:
                # Extract price fields if they exist
                if "Price" in row and isinstance(row["Price"], (int, float)):
                    price_info["Price"] = row["Price"]
                if "Price / pc" in row and isinstance(row["Price / pc"], str):
                    price_info["Price / pc"] = row["Price / pc"]
                # Break on first matching row per block
                break
    return price_info


def ensure_session_defaults():
    st.session_state.setdefault("page", "login")
    st.session_state.setdefault("wishlist", [])
    st.session_state.setdefault("selected_sku", None)
    st.session_state.setdefault("username", "")


def set_page(page_name: str):
    st.session_state["page"] = page_name
    st.rerun()


def go_product(sku: str):
    st.session_state["selected_sku"] = sku
    set_page("product")


# ------------------------------ UI Components ---------------------------------


def top_nav(products_count: int):
    """Header with app title and Wishlist button. Visible on all pages except login."""
    if st.session_state.get("page") == "login":
        return
    left, right = st.columns([3, 1])
    with left:
        st.markdown(
            f"### Piercing Shop Inventory — {products_count} products"
        )
    with right:
        if st.button(f"🧾 Wishlist ({len(st.session_state['wishlist'])})", use_container_width=True):
            set_page("wishlist")


def sidebar_filters(all_tags: List[str]) -> List[str]:
    st.sidebar.markdown("### Filters")
    return st.sidebar.multiselect("Filter by tags", options=all_tags, default=[])


def render_product_card(p: Dict[str, Any]):
    img = best_image(p)
    st.image(img, use_column_width=True, caption=None)
    st.caption(p.get("title") or p.get("sku"))
    if st.button("View", key=f"view_{p.get('sku')}", use_container_width=True):
        go_product(p.get("sku"))


def render_gallery(products: List[Dict[str, Any]]):
    st.subheader("Product Gallery")
    if not products:
        st.info("No products match your filters.")
        return
    n_cols = 4
    rows = (len(products) + n_cols - 1) // n_cols
    for r in range(rows):
        cols = st.columns(n_cols)
        for c in range(n_cols):
            idx = r * n_cols + c
            if idx >= len(products):
                break
            with cols[c]:
                render_product_card(products[idx])


def render_product_page(product: Dict[str, Any]):
    st.button("← Back to Gallery", on_click=lambda: set_page("main"))
    st.header(product.get("title") or product.get("sku"))
    st.write(f"**SKU:** {product.get('sku', '')}")
    if product.get("description"):
        st.write(product["description"])

    tags = product.get("tags") or []
    if tags:
        st.write("**Tags:** " + ", ".join(tags))

    img = best_image(product)
    if img:
        st.image(img, use_column_width=True, caption="Main image")

    # --- Variant selectors ---
    axes = build_variant_axes(product)
    selections: Dict[str, str] = {}
    variant_preview_images: List[str] = []

    if axes:
        st.subheader("Select options")
    for ax in axes:
        label = ax["label"]
        options = ax["options"] or ["Unspecified"]
        key = f"sel_{product.get('sku')}_{label}"
        # Default to the first option
        default_index = 0
        if key not in st.session_state:
            st.session_state[key] = options[default_index]
        chosen = st.selectbox(label, options, index=options.index(st.session_state[key]) if st.session_state[key] in options else 0, key=key)
        selections[label] = chosen
        # Collect variant-specific image if present
        img_map = ax.get("image_map") or {}
        vimg = img_map.get(chosen)
        if vimg:
            variant_preview_images.append(vimg)

    # Show the first variant preview image (if any)
    if variant_preview_images:
        st.image(variant_preview_images[0], caption="Selected option preview", use_column_width=False)

    # Optional: show price info if the selected combination maps to a priced row
    price_info = compute_price_info(product, selections)
    if price_info:
        st.markdown("**Price info (from selection):**")
        for k, v in price_info.items():
            st.write(f"- {k}: {v}")

    # Add to wishlist
    def add_to_wishlist():
        item = {
            "sku": product.get("sku"),
            "title": product.get("title") or product.get("sku"),
            "main_image": best_image(product),
            "url": product.get("url"),
            "selections": selections.copy(),
            "variant_image": variant_preview_images[0] if variant_preview_images else None,
            "price_info": price_info,
        }
        st.session_state["wishlist"].append(item)
        st.success("Added to wishlist!")

    st.button("➕ Add to Wishlist", type="primary", on_click=add_to_wishlist)


def render_wishlist():
    st.button("← Back to Gallery", on_click=lambda: set_page("main"))
    st.header("Your Wishlist")

    # Developer note about persistence:
    st.info(
        "Prototype note: Wishlist lives only in this browser session via `st.session_state`.\n\n"
        "In a production Django/Flask app, you'd persist wishlists to a database (e.g., Postgres) "
        "linked to the authenticated user, so they survive refresh and are available across devices."
    )

    items = st.session_state.get("wishlist", [])
    if not items:
        st.write("Your wishlist is empty.")
        return

    for i, item in enumerate(items):
        with st.container(border=True):
            cols = st.columns([1, 3, 1])
            with cols[0]:
                img = item.get("variant_image") or item.get("main_image")
                if img:
                    st.image(img, use_column_width=True)
            with cols[1]:
                st.markdown(f"**{item.get('title')}**")
                st.write(f"SKU: {item.get('sku')}")
                if item.get("selections"):
                    st.write("**Selected options:**")
                    for k, v in item["selections"].items():
                        st.write(f"- {k}: {v}")
                if item.get("price_info"):
                    st.write("**Price info:**")
                    for k, v in item["price_info"].items():
                        st.write(f"- {k}: {v}")
                if item.get("url"):
                    st.link_button("Open product page", item["url"])
            with cols[2]:
                if st.button("Remove", key=f"remove_{i}", use_container_width=True):
                    del st.session_state["wishlist"][i]
                    st.rerun()


# ------------------------------ Pages -----------------------------------------


def page_login():
    st.title("🔐 Employee Login (Placeholder)")

    # This is a **placeholder**. It does not authenticate the user!
    # In production (Django/Flask/FastAPI):
    #   • Validate credentials against a user store (DB with hashed passwords).
    #   • Create a server-side session or issue a signed JWT.
    #   • Set secure cookies (HttpOnly, Secure, SameSite) and enforce CSRF protection.
    #   • Implement password reset and multi-factor authentication where appropriate.
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login", type="primary"):
        # We accept anything and "log in".
        st.session_state["username"] = username.strip() or "Demo User"
        set_page("main")

    st.caption(
        "This login screen is a demo. A real app requires a secure authentication backend."
    )


def page_main(data: Dict[str, Any]):
    products = get_all_products(data)
    top_nav(len(products))
    tags = all_unique_tags(products)
    selected_tags = sidebar_filters(tags)
    filtered = filter_by_tags(products, selected_tags)
    render_gallery(filtered)


def page_product(data: Dict[str, Any]):
    products = get_all_products(data)
    top_nav(len(products))
    sku = st.session_state.get("selected_sku")
    if not sku:
        st.warning("No product selected.")
        st.button("Back to Gallery", on_click=lambda: set_page("main"))
        return
    prod = next((p for p in products if p.get("sku") == sku), None)
    if not prod:
        st.error("Selected product not found.")
        st.button("Back to Gallery", on_click=lambda: set_page("main"))
        return
    render_product_page(prod)


def page_wishlist(data: Dict[str, Any]):
    products = get_all_products(data)
    top_nav(len(products))
    render_wishlist()


# ------------------------------ Main Entry ------------------------------------


def main():
    ensure_session_defaults()

    data = load_products("products.json")

    # Global sidebar navigation (except on login) for convenience
    if st.session_state.get("page") != "login":
        with st.sidebar.expander("Navigation", expanded=True):
            if st.button("🏠 Main Page", use_container_width=True):
                set_page("main")
            if st.button("🧾 Wishlist", use_container_width=True):
                set_page("wishlist")

    page = st.session_state.get("page", "login")
    if page == "login":
        page_login()
    elif page == "main":
        page_main(data)
    elif page == "product":
        page_product(data)
    elif page == "wishlist":
        page_wishlist(data)
    else:
        set_page("login")


if __name__ == "__main__":
    main()
 
