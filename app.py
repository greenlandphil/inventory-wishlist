# app.py
# -----------------------------------------------------------------------------
# Piercing Shop Inventory & Wishlist (Streamlit Prototype) — Quantities
# Remote-images only; variant swatch fix; price code removed
# -----------------------------------------------------------------------------

import json
import os
import re
import hashlib
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import urlparse

import streamlit as st

# ------------------------------ Utilities & Data ------------------------------


@st.cache_data(show_spinner=False)
def load_products(path: str = "products.json") -> Dict[str, Any]:
    if not os.path.exists(path):
        st.error(
            f"Could not find `{path}`. Make sure it exists next to app.py "
            f"and contains the provided product JSON."
        )
        st.stop()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _infer_base_url(product: Dict[str, Any]) -> str:
    """
    Try to infer a sensible base url for fixing relative image paths.
    Preference order: product['url'] domain → product['main_image'] domain → achadirect.
    """
    fallback = "https://www.achadirect.com"
    for k in ("url", "main_image"):
        u = (product.get(k) or "").strip()
        if u.startswith("http"):
            try:
                p = urlparse(u)
                if p.scheme in ("http", "https") and p.netloc:
                    return f"{p.scheme}://{p.netloc}"
            except Exception:
                pass
    return fallback


def normalize_image_url(img: Optional[str], base_url: Optional[str] = None) -> Optional[str]:
    """
    Normalize possibly-relative or protocol-relative URLs to absolute HTTPS URLs.
    """
    if not img:
        return None
    s = str(img).strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("//"):
        return "https:" + s
    if s.startswith("/"):
        return (base_url or "https://www.achadirect.com") + s
    # already some path or data URI; return as-is
    return s


def show_image_safe(img: Optional[str], caption: Optional[str] = None, fill: bool = True):
    """
    Safe wrapper around st.image that tolerates None/invalid images.
    """
    if not img:
        st.caption("🖼️ Image not available")
        return
    try:
        st.image(img, use_container_width=fill, caption=caption)
    except Exception:
        st.caption("🖼️ Image failed to load")


def best_image(product: Dict[str, Any]) -> Optional[str]:
    # Remote-only main image
    return product.get("main_image")


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
    label = (raw_label or "").strip()
    if option_names and all(_MM_RE.match(str(x or "")) for x in option_names):
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


# Keep this only to IGNORE price-like columns from selection axes.
_PRICEY_COLS = {
    "price", "price / pc", "price/pc", "price per pc",
    "price per pair", "price per piece", "price / pair"
}


def _clean_option_name(name: Any) -> str:
    s = ("" if name is None else str(name)).strip()
    return s if s else "Unspecified"


def build_variant_axes(product: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Merge both variant blocks into logical axes with:
      - label
      - options (list of names)
      - image_map (name -> absolute URL)
    Remote URLs only, normalized.
    """
    base_url = _infer_base_url(product)
    axes_by_label: Dict[str, Dict[str, Any]] = {}

    blocks = product.get("variants") or []
    for idx, v in enumerate(blocks):
        vtype = v.get("type")

        if vtype == "variant_1":
            headers = v.get("headers") or []
            items = v.get("items") or []
            for h in headers:
                if (h or "").strip().lower() in _PRICEY_COLS:
                    continue  # ignore price-like columns entirely

                options_order: List[str] = []
                image_map: Dict[str, str] = {}
                for row in items:
                    val = _clean_option_name(row.get(h))
                    if val and val not in options_order:
                        options_order.append(val)
                    # Some variant_1 rows include an 'image' swatch (e.g., Crystal Color)
                    remote = normalize_image_url(row.get("image"), base_url)
                    if remote:
                        image_map[val] = remote

                normalized = normalize_axis_label(h, options_order)
                bucket = axes_by_label.setdefault(
                    normalized,
                    {"label": normalized, "options": [], "image_map": {}, "source_blocks": [], "kind": "merged"},
                )
                for opt in options_order:
                    if opt not in bucket["options"]:
                        bucket["options"].append(opt)
                bucket["image_map"].update(image_map)
                bucket["source_blocks"].append(idx)

        elif vtype == "variant_2":
            label = v.get("label") or "Color"
            options = v.get("options") or []
            opt_names: List[str] = []
            image_map: Dict[str, str] = {}
            for opt in options:
                name = _clean_option_name(opt.get("name"))
                opt_names.append(name)
                remote = normalize_image_url(opt.get("image"), _infer_base_url(product))
                if remote:
                    image_map[name] = remote

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

    priority = {"Length": 0, "Size": 1, "Gauge": 2, "Color": 3, "Packing Option": 4, "Rack": 5}
    return sorted(axes_by_label.values(), key=lambda ax: (priority.get(ax["label"], 99), ax["label"]))


# ------------------------------ Wishlist Helpers ------------------------------


def ensure_session_defaults():
    st.session_state.setdefault("page", "login")
    st.session_state.setdefault("wishlist", {})  # dict keyed by item_key
    st.session_state.setdefault("selected_sku", None)
    st.session_state.setdefault("username", "")

    # Backward-compat: migrate old list → dict (quantity=1 each)
    if isinstance(st.session_state["wishlist"], list):
        old_list = st.session_state["wishlist"]
        st.session_state["wishlist"] = {}
        for it in old_list:
            key = make_item_key(it.get("sku"), it.get("selections") or {})
            st.session_state["wishlist"][key] = {
                **it,
                "quantity": st.session_state["wishlist"].get(key, {}).get("quantity", 0) + 1,
            }


def set_page(page_name: str):
    st.session_state["page"] = page_name
    st.rerun()


def go_product(sku: str):
    st.session_state["selected_sku"] = sku
    set_page("product")


def selections_key(selections: Dict[str, str]) -> str:
    if not selections:
        return ""
    parts = [f"{k}:{v}" for k, v in sorted(selections.items(), key=lambda kv: kv[0].lower())]
    return "|".join(parts)


def make_item_key(sku: Optional[str], selections: Dict[str, str]) -> str:
    base = f"{sku or ''}||{selections_key(selections)}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def wishlist_counts() -> Tuple[int, int]:
    wl = st.session_state.get("wishlist", {})
    if not isinstance(wl, dict):
        return (len(wl), len(wl))
    unique = len(wl)
    total_qty = sum(int(v.get("quantity", 1)) for v in wl.values())
    return unique, total_qty


def wishlist_add(item: Dict[str, Any]):
    """
    Add or increment an item (by SKU + selections).
    Stores 'variant_image' (a swatch or per-option preview if available).
    """
    wl: Dict[str, Dict[str, Any]] = st.session_state["wishlist"]
    key = make_item_key(item.get("sku"), item.get("selections") or {})
    if key in wl:
        wl[key]["quantity"] = int(wl[key].get("quantity", 1)) + 1
        wl[key]["variant_image"] = item.get("variant_image") or wl[key].get("variant_image")
    else:
        wl[key] = {**item, "quantity": 1}
    st.session_state["wishlist"] = wl


def wishlist_inc(key: str):
    wl: Dict[str, Dict[str, Any]] = st.session_state.get("wishlist", {})
    if key in wl:
        wl[key]["quantity"] = int(wl[key].get("quantity", 1)) + 1
        st.session_state["wishlist"] = wl
        st.rerun()


def wishlist_dec(key: str):
    wl: Dict[str, Dict[str, Any]] = st.session_state.get("wishlist", {})
    if key in wl:
        new_q = int(wl[key].get("quantity", 1)) - 1
        if new_q <= 0:
            del wl[key]
        else:
            wl[key]["quantity"] = new_q
        st.session_state["wishlist"] = wl
        st.rerun()


def wishlist_remove(key: str):
    wl: Dict[str, Dict[str, Any]] = st.session_state.get("wishlist", {})
    if key in wl:
        del wl[key]
        st.session_state["wishlist"] = wl
        st.rerun()


# ------------------------------ UI Components ---------------------------------


def top_nav(products_count: int):
    if st.session_state.get("page") == "login":
        return
    unique, total_qty = wishlist_counts()
    left, right = st.columns([3, 1])
    with left:
        st.markdown(f"### Piercing Shop Inventory — {products_count} products")
    with right:
        if st.button(f"🧾 Wishlist ({unique})", use_container_width=True):
            set_page("wishlist")


def sidebar_filters(all_tags: List[str]) -> List[str]:
    st.sidebar.markdown("### Filters")
    return st.sidebar.multiselect("Filter by tags", options=all_tags, default=[])


def render_product_card(p: Dict[str, Any]):
    img = normalize_image_url(best_image(p), _infer_base_url(p))
    show_image_safe(img, caption=None, fill=True)
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

    # main product image
    main_img = normalize_image_url(best_image(product), _infer_base_url(product))
    show_image_safe(main_img, caption="Main image", fill=True)

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
        if key not in st.session_state:
            st.session_state[key] = options[0]

        chosen = st.selectbox(
            label,
            options,
            index=options.index(st.session_state[key]) if st.session_state[key] in options else 0,
            key=key,
        )
        selections[label] = chosen

        # swatch/preview image for this option (if any)
        img_map = ax.get("image_map") or {}
        vimg = img_map.get(chosen)
        if vimg:
            variant_preview_images.append(normalize_image_url(vimg, _infer_base_url(product)))

    # show the first available variant preview (often Color or Crystal Color)
    if variant_preview_images:
        show_image_safe(variant_preview_images[0], caption="Selected option preview", fill=False)

    # Add to wishlist
    def on_add():
        item = {
            "sku": product.get("sku"),
            "title": product.get("title") or product.get("sku"),
            "main_image": normalize_image_url(best_image(product), _infer_base_url(product)),
            "url": product.get("url"),
            "selections": selections.copy(),
            # store one best swatch/variant preview if available
            "variant_image": variant_preview_images[0] if variant_preview_images else None,
        }
        wishlist_add(item)
        st.success("Added to wishlist!")

    st.button("➕ Add to Wishlist", type="primary", on_click=on_add)


def render_wishlist():
    st.button("← Back to Gallery", on_click=lambda: set_page("main"))
    st.header("Your Wishlist")

    st.info(
        "Prototype note: Wishlist lives only in this browser session via `st.session_state`.\n\n"
        "In production (Django/Flask), persist wishlists to a database linked to the authenticated user."
    )

    wl: Dict[str, Dict[str, Any]] = st.session_state.get("wishlist", {})
    if not wl:
        st.write("Your wishlist is empty.")
        return

    unique, total_qty = wishlist_counts()
    st.caption(f"**Unique lines:** {unique} | **Total quantity selected:** {total_qty}")

    for key, item in wl.items():
        qty = int(item.get("quantity", 1))
        with st.container(border=True):
            cols = st.columns([1, 3, 1])
            with cols[0]:
                show_image_safe(item.get("main_image"), fill=True)

            with cols[1]:
                st.markdown(f"**{item.get('title')}**")
                st.write(f"SKU: {item.get('sku')}")
                if item.get("selections"):
                    st.write("**Selected options:**")
                    # Render each selection; if a swatch image exists, show it inline
                    for k, v in item["selections"].items():
                        row_cols = st.columns([3, 2])
                        with row_cols[0]:
                            st.write(f"- {k}: {v}")
                        with row_cols[1]:
                            if item.get("variant_image") and k.lower() in {"color", "crystal color"}:
                                # small swatch beside the color-like selection
                                show_image_safe(item["variant_image"], fill=False)
                if item.get("url"):
                    st.link_button("Open product page", item["url"])

            with cols[2]:
                qcols = st.columns([1, 1, 2])
                with qcols[0]:
                    st.button("➖", key=f"dec_{key}", use_container_width=True, on_click=wishlist_dec, args=(key,))
                with qcols[1]:
                    st.button("➕", key=f"inc_{key}", use_container_width=True, on_click=wishlist_inc, args=(key,))
                with qcols[2]:
                    st.markdown(f"**Qty:** {qty}")
                st.divider()
                st.button("Remove line", key=f"remove_{key}", use_container_width=True, on_click=wishlist_remove, args=(key,))


# ------------------------------ Pages -----------------------------------------


def page_login():
    st.title("🔐 Employee Login (Placeholder)")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login", type="primary"):
        st.session_state["username"] = username.strip() or "Demo User"
        set_page("main")

    st.caption("This login screen is a demo. A real app requires a secure authentication backend.")


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
