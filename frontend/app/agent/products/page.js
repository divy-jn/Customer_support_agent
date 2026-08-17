"use client";

import { useState, useEffect } from "react";
import { API_BASE } from "../../lib/api";

export default function ProductsPage() {
  const [products, setProducts] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  const fetchProducts = async (query = "") => {
    setLoading(true);
    try {
      const q = query || "Pro";
      const res = await fetch(
        `${API_BASE}/api/v1/products/search?q=${encodeURIComponent(q)}`
      );
      const data = await res.json();
      setProducts(Array.isArray(data) ? data : data.value || []);
    } catch {
      setProducts([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    (async () => {
      await fetchProducts();
    })();
  }, []);

  const handleSearch = (e) => {
    e.preventDefault();
    fetchProducts(search);
  };

  const categoryColors = {
    Smartphones: "#8b5cf6",
    Tablets: "#6366f1",
    Laptops: "#3b82f6",
    Desktops: "#0ea5e9",
    "Smart Home": "#22c55e",
    Audio: "#f59e0b",
    Gaming: "#ef4444",
    "Gaming Accessories": "#f97316",
    Wearables: "#ec4899",
    "Home Appliances": "#14b8a6",
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Product Catalog</h2>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Browse and search products in our inventory
          </p>
        </div>
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search products..."
            className="bg-[var(--bg-input)] border border-[var(--border)] rounded-xl px-4 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--border-glow)] w-56"
          />
          <button type="submit" className="btn-glow px-4 py-2 text-sm">
            Search
          </button>
        </form>
      </div>

      {/* Products Grid */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-8 h-8 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
        </div>
      ) : products.length === 0 ? (
        <div className="text-center py-20 text-[var(--text-muted)]">
          No products found
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {products.map((product) => {
            const catColor =
              categoryColors[product.category] || "#6366f1";
            return (
              <div
                key={product.id}
                className="glass-card glass-card-hover p-5 animate-fade-in flex flex-col"
              >
                {/* Category tag */}
                <div className="flex items-center justify-between mb-3">
                  <span
                    className="text-[10px] font-semibold uppercase tracking-wider px-2.5 py-1 rounded-full"
                    style={{
                      background: `${catColor}15`,
                      color: catColor,
                    }}
                  >
                    {product.category}
                  </span>
                  <span
                    className={`text-xs font-medium ${
                      product.in_stock
                        ? "text-emerald-400"
                        : "text-red-400"
                    }`}
                  >
                    {product.in_stock
                      ? `${product.stock} in stock`
                      : "Out of stock"}
                  </span>
                </div>

                {/* Product info */}
                <h3 className="text-base font-semibold text-[var(--text-primary)] mb-1">
                  {product.name}
                </h3>
                <p className="text-xs text-[var(--text-muted)] mb-4 flex-1 line-clamp-2">
                  {product.description}
                </p>

                {/* Price */}
                <div className="flex items-center justify-between pt-3 border-t border-[var(--border)]">
                  <span className="text-xl font-bold gradient-text">
                    ${product.price}
                  </span>
                  <span className="text-xs text-[var(--text-muted)]">
                    ID: {product.id}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
