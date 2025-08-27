# Piercing Inventory & Wishlist (Streamlit)

Streamlit prototype for a piercing shop: browse products, view variant options, and save a per-session wishlist.  
**Data source:** `products.json`

## Features
- **Login (placeholder):** non-functional screen (demo only).
- **Product Gallery:** grid with tag filtering (sidebar).
- **Product Page:** title, images, tags, dynamic variant dropdowns (Length/Color/etc.) with swatch preview.
- **Wishlist:** add items with selected variants; remove items.

> ⚠️ Prototype notes:
> - Streamlit session state is ephemeral. In production, use a backend (Django/Flask/FastAPI) with auth and a database to persist wishlists.
> - The login is a placeholder and not secure; In production, implement proper authentication.

## Quickstart
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
streamlit run app.py
