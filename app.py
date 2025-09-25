# app.py
# -----------------------------------------------------------------------------
# Piercing Shop Inventory & Wishlist (Streamlit Prototype)
# - Multi-image schema only: main_images[] + main_images_local[]
# - Product page CAROUSEL (Prev/Next + dots)
# - Remote-first images with LOCAL fallback (main + swatches)
# - Pricing removed (headers ignored; no price UI)
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
    Infer a base URL for resolving protocol/relative paths.
    Preference: product['url'] domain → first main image domain → achadirect.com
    """
    fallback = "https://www.achadirect.com"

    u = (product.get("url") or "").strip()
    if u.startswith("http"):
        try:
            p = urlparse(u)
            if p.scheme in ("http", "https") and p.netloc:
                return f"{p.scheme}://{p.netloc}"
        except Exception:
            pass

    imgs = product.get("main_images") or []
    if imgs:
        u = str(imgs[0]).strip()
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


def normalize_local_path(path: Optional[str]) -> Optional[str]:
    """
    Normalize Windows-style backslashes from scraped JSON to OS-friendly path.
    """
    if not path:
        return None
    return path.replace("\\", "/")


def show_image_with_fallback(remote: Optional[str], local: Optional[str], caption: Optional[str] = None, fill: bool = True):
    """
    Try remote first; if display fails, try local. If both fail, show a placeholder caption.
    """
    # Attempt remote first
    if remote:
        try:
            st.image(remote, use_container_width=fill, caption=caption)
            return
        except Exception:
            pass
    # Fallback to local
    if local:
        try:
            st.image(local, use_container_width=fill, caption=caption)
            return
        except Exception:
            pass
    st.caption("🖼️ Image not available")


def build_main_image_pairs(product: Dict[str, Any]) -> List[Tuple[Optional[str], Optional[str]]]:
    """
    Build ordered list of (remote, local) image pairs using:
      - main_images:        List[str] of remote URLs
      - main_images_local:  List[str] of local paths
    Pairing is by index; if lists differ in length, missing side becomes None.
    """
    base = _infer_base_url(product)
    remotes = [normalize_image_url(u, base) for u in (product.get("main_images") or [])]
    locals_ = [normalize_local_path(p) for p in (product.get("main_images_local") or [])]

    n = max(len(remotes), len(locals_))
    pairs: List[Tuple[Optional[str], Optional[str]]] = []
    for i in range(n):
        r = remotes[i] if i < len(remotes) else None
        l = locals_[i] if i < len(locals_) else None
        if r or l:
            pairs.append((r, l))

    if not pairs:
        pairs.append((None, None))

    # Deduplicate while preserving order
    dedup: List[Tuple[Optional[str], Optional[str]]] = []
    seen = set()
    for pr in pairs:
        key = (pr[0] or "", pr[1] or "")
        if key not in seen:
            seen.add(key)
            dedup.append(pr)
    return dedup


# ------------------------------ Filtering & Variants ---------------------------

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


# Ignore price-like columns when building axes (we don't show prices)
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
      - image_map: name -> {"remote": str|None, "local": str|None}
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
                image_map: Dict[str, Dict[str, Optional[str]]] = {}
                for row in items:
                    val = _clean_option_name(row.get(h))
                    if val and val not in options_order:
                        options_order.append(val)
                    # Some variant_1 rows include a swatch image pair
                    remote = normalize_image_url(row.get("image"), base_url)
                    local = normalize_local_path(row.get("image_local"))
                    if remote or local:
                        image_map[val] = {"remote": remote, "local": local}

                normalized = normalize_axis_label(h, options_order)
                bucket = axes_by_label.setdefault(
                    normalized,
                    {"label": normalized, "options": [], "image_map": {}, "source_blocks": [], "kind": "merged"},
                )
                for opt in options_order:
                    if opt not in bucket["options"]:
                        bucket["options"].append(opt)
                for k, vdict in image_map.items():
                    bucket["image_map"][k] = vdict
                bucket["source_blocks"].append(idx)

        elif vtype == "variant_2":
            label = v.get("label") or "Color"
            options = v.get("options") or []
            opt_names: List[str] = []
            image_map: Dict[str, Dict[str, Optional[str]]] = {}
            for opt in options:
                name = _clean_option_name(opt.get("name"))
                opt_names.append(name)
                remote = normalize_image_url(opt.get("image"), base_url)
                local = normalize_local_path(opt.get("image_local"))
                if remote or local:
                    image_map[name] = {"remote": remote, "local": local}

            normalized = normalize_axis_label(label, opt_names)
            bucket = axes_by_label.setdefault(
                normalized,
                {"label": normalized, "options": [], "image_map": {}, "source_blocks": [], "kind": "merged"},
            )
            for name in opt_names:
                if name not in bucket["options"]:
                    bucket["options"].append(name)
            for k, vdict in image_map.items():
                bucket["image_map"][k] = vdict
            bucket["source_blocks"].append(idx)

    priority = {"Length": 0, "Size": 1, "Gauge": 2, "Color": 3, "Packing Option": 4, "Rack": 5}
    return sorted(axes_by_label.values(), key=lambda ax: (priority.get(ax["label"], 99), ax["label"]))


# ------------------------------ Wishlist Helpers ------------------------------

def ensure_session_defaults():
    st.session_state.setdefault("page", "login")
    st.session_state.setdefault("wishlist", {})  # dict keyed by item_key
    st.session_state.setdefault("selected_sku", None)
    st.session_state.setdefault("username", "")

    # If any legacy list exists (rare), migrate to dict (quantity=1 each)
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
    Stores both variant and main image pairs for robust re-display.
    """
    wl: Dict[str, Dict[str, Any]] = st.session_state["wishlist"]
    key = make_item_key(item.get("sku"), item.get("selections") or {})
    if key in wl:
        wl[key]["quantity"] = int(wl[key].get("quantity", 1)) + 1
        for k in ("variant_image_remote", "variant_image_local", "main_image_remote", "main_image_local"):
            if item.get(k):
                wl[key][k] = item[k]
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
    unique, _total_qty = wishlist_counts()
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
    pairs = build_main_image_pairs(p)
    r0, l0 = pairs[0] if pairs else (None, None)
    show_image_with_fallback(r0, l0, caption=None, fill=True)
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


# ------------------------------ Carousel --------------------------------------

def render_image_carousel(pairs: List[Tuple[Optional[str], Optional[str]]], key_prefix: str):
    """
    Simple carousel with Prev/Next buttons and dot indicators.
    Displays the current image with remote-first + local fallback.
    """
    n = len(pairs)
    if n == 0:
        show_image_with_fallback(None, None, caption="Main image", fill=True)
        return
    if n == 1:
        r, l = pairs[0]
        show_image_with_fallback(r, l, caption="Main image", fill=True)
        return

    idx_key = f"{key_prefix}_carousel_idx"
    st.session_state.setdefault(idx_key, 0)
    cur = int(st.session_state[idx_key]) % n

    # Controls row: Prev | Image | Next
    cprev, cimg, cnext = st.columns([1, 8, 1])

    def set_idx(new_i: int):
        st.session_state[idx_key] = new_i % n

    with cprev:
        if st.button("◀", key=f"{key_prefix}_prev", use_container_width=True):
            set_idx(cur - 1)

    with cimg:
        r, l = pairs[cur]
        show_image_with_fallback(r, l, caption=f"{cur+1}/{n}", fill=True)

    with cnext:
        if st.button("▶", key=f"{key_prefix}_next", use_container_width=True):
            set_idx(cur + 1)

    # Dot indicators (clickable)
    dot_cols = st.columns(n if n <= 8 else 8)  # cap row width; if more than 8, still show first 8
    for i in range(min(n, 8)):
        with dot_cols[i]:
            label = "●" if i == cur else "○"
            st.button(label, key=f"{key_prefix}_dot_{i}", on_click=set_idx, args=(i,), use_container_width=True)


# ------------------------------ Product & Wishlist Pages -----------------------

def render_product_page(product: Dict[str, Any]):
    st.button("← Back to Gallery", on_click=lambda: set_page("main"))
    st.header(product.get("title") or product.get("sku"))
    st.write(f"**SKU:** {product.get('sku', '')}")
    if product.get("description"):
        st.write(product["description"])

    tags = product.get("tags") or []
    if tags:
        st.write("**Tags:** " + ", ".join(tags))

    # --- Main images: carousel ---
    pairs = build_main_image_pairs(product)
    st.subheader("Images")
    render_image_carousel(pairs, key_prefix=f"{product.get('sku') or 'SKU'}")

    # --- Variant selectors ---
    axes = build_variant_axes(product)
    selections: Dict[str, str] = {}
    variant_preview_pair: Tuple[Optional[str], Optional[str]] = (None, None)

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

        # swatch/preview image pair for this option (if any)
        img_map = ax.get("image_map") or {}
        vdict = img_map.get(chosen)  # {'remote': ..., 'local': ...}
        if vdict and not any(variant_preview_pair):
            variant_preview_pair = (
                vdict.get("remote"),
                vdict.get("local"),
            )

    # show the first available variant preview (often Color or Crystal Color)
    if any(variant_preview_pair):
        show_image_with_fallback(
            variant_preview_pair[0],
            normalize_local_path(variant_preview_pair[1]),
            caption="Selected option preview",
            fill=False
        )

    # Add to wishlist (store the FIRST main image pair as thumbnail)
    def on_add():
        main_remote, main_local = pairs[0] if pairs else (None, None)
        item = {
            "sku": product.get("sku"),
            "title": product.get("title") or product.get("sku"),
            "main_image_remote": main_remote,
            "main_image_local": normalize_local_path(main_local),
            "url": product.get("url"),
            "selections": selections.copy(),
            "variant_image_remote": variant_preview_pair[0],
            "variant_image_local": normalize_local_path(variant_preview_pair[1]),
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
                show_image_with_fallback(
                    item.get("main_image_remote"),
                    normalize_local_path(item.get("main_image_local")),
                    fill=True
                )

            with cols[1]:
                st.markdown(f"**{item.get('title')}**")
                st.write(f"SKU: {item.get('sku')}")
                if item.get("selections"):
                    st.write("**Selected options:**")
                    for k, v in item["selections"].items():
                        row_cols = st.columns([3, 2])
                        with row_cols[0]:
                            st.write(f"- {k}: {v}")
                        with row_cols[1]:
                            # show swatch beside color-like selections, remote-first with local fallback
                            if k.lower() in {"color", "crystal color"}:
                                show_image_with_fallback(
                                    item.get("variant_image_remote"),
                                    normalize_local_path(item.get("variant_image_local")),
                                    fill=False
                                )
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
    data = load_products("products.json")  # Use your multi-image JSON here

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
