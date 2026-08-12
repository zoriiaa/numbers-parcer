import React, { useState, useEffect } from 'react';
import { Search, Upload, Package, RefreshCw, X, Eye } from 'lucide-react';

const API_URL = 'https://numbers-parcer.onrender.com';

function App() {
  const [data, setData] = useState({ categories: [], products: [] });
  const [search, setSearch] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('All');
  const [uploading, setUploading] = useState(false);
  const [previewImage, setPreviewImage] = useState(null);

  const fetchProducts = async () => {
    try {
      const res = await fetch(`${API_URL}/api/products`);
      if (res.ok) {
        const json = await res.json();
        setData(json);
      }
    } catch (e) {
      console.error("Помилка завантаження даних:", e);
    }
  };

  useEffect(() => {
    fetchProducts();
  }, []);

  const handleFileUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    setUploading(true);

    try {
      // Надсилаємо файл бінарно напрямую без FormData
      const res = await fetch(`${API_URL}/api/upload?filename=${encodeURIComponent(file.name)}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
        },
        body: file,
      });

      const json = await res.json();
      if (res.ok) {
        await fetchProducts();
      } else {
        alert(`Помилка: ${json.detail || 'Не вдалося обробити файл'}`);
      }
    } catch (e) {
      alert('Помилка з\'єднання з сервером');
    } finally {
      setUploading(false);
      e.target.value = '';
    }
  };

  const filteredProducts = (data.products || []).filter((p) => {
    const matchesSearch = p.title?.toLowerCase().includes(search.toLowerCase()) ||
                          p.sku?.toLowerCase().includes(search.toLowerCase());
    const matchesCategory = selectedCategory === 'All' || p.category === selectedCategory;
    return matchesSearch && matchesCategory;
  });

  const formatPrice = (val) => {
    return Number(val).toLocaleString('uk-UA', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 pb-12 font-sans antialiased">
      <header className="sticky top-0 z-20 bg-white shadow-sm border-b border-slate-200">
        <div className="max-w-md mx-auto px-4 py-3">
          <div className="flex justify-between items-center mb-3">
            <h1 className="text-lg font-bold flex items-center gap-2 text-slate-800">
              <Package className="text-blue-600" size={22} /> База Товарів
            </h1>

            <label className="cursor-pointer bg-blue-600 active:bg-blue-700 text-white px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm transition">
              {uploading ? <RefreshCw className="animate-spin" size={14} /> : <Upload size={14} />}
              <span>{uploading ? 'Обробка...' : '.numbers'}</span>
              <input type="file" accept=".numbers,.xlsx,.xls" onChange={handleFileUpload} className="hidden" />
            </label>
          </div>

          <div className="relative mb-2">
            <Search className="absolute left-3.5 top-2.5 text-slate-400" size={16} />
            <input
              type="text"
              placeholder="Пошук назви чи артикула..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-9 pr-8 py-2 bg-slate-100 border-0 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
            />
            {search && (
              <button onClick={() => setSearch('')} className="absolute right-2.5 top-2.5 text-slate-400">
                <X size={16} />
              </button>
            )}
          </div>

          <div className="flex gap-1.5 overflow-x-auto no-scrollbar py-1 -mx-4 px-4">
            <button
              onClick={() => setSelectedCategory('All')}
              className={`px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap transition ${
                selectedCategory === 'All' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600'
              }`}
            >
              Всі ({data.products?.length || 0})
            </button>
            {data.categories?.map((cat) => (
              <button
                key={cat}
                onClick={() => setSelectedCategory(cat)}
                className={`px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap transition ${
                  selectedCategory === cat ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="max-w-md mx-auto p-3 space-y-2.5">
        {filteredProducts.length === 0 ? (
          <div className="text-center py-16 text-slate-400">
            <Package size={48} className="mx-auto mb-2 opacity-30" />
            <p className="text-sm">
              {data.products?.length === 0
                ? 'Завантажте файл .numbers через кнопку зверху.'
                : 'Товарів не знайдено.'}
            </p>
          </div>
        ) : (
          filteredProducts.map((product) => (
            <div key={product.id} className="bg-white rounded-2xl p-3 shadow-sm border border-slate-100 flex gap-3 items-center">
              <div className="w-20 h-20 bg-slate-50 rounded-xl flex-shrink-0 flex items-center justify-center border border-slate-100 overflow-hidden relative">
                {product.image ? (
                  <button
                    onClick={() => setPreviewImage(`${API_URL}${product.image}`)}
                    className="w-full h-full relative group"
                  >
                    <img src={`${API_URL}${product.image}`} alt={product.title} className="w-full h-full object-cover" />
                    <div className="absolute inset-0 bg-black/20 flex items-center justify-center opacity-0 group-hover:opacity-100 transition">
                      <Eye size={18} className="text-white" />
                    </div>
                  </button>
                ) : (
                  <Package className="text-slate-300" size={28} />
                )}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded-md truncate max-w-[150px]">
                    {product.category}
                  </span>
                </div>

                <h3 className="font-medium text-xs text-slate-800 line-clamp-2 leading-snug">{product.title}</h3>
                <p className="text-[11px] text-slate-400 mt-0.5">Арт: {product.sku || '—'}</p>

                <div className="flex justify-between items-end mt-1.5">
                  <span className="font-bold text-slate-900 text-sm">{formatPrice(product.price)} ₴</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${
                    product.count > 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-600'
                  }`}>
                    {product.count > 0 ? `${product.count} шт` : 'Немає'}
                  </span>
                </div>
              </div>
            </div>
          ))
        )}
      </main>

      {previewImage && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setPreviewImage(null)}
        >
          <div className="relative max-w-full max-h-full">
            <img src={previewImage} alt="Прев'ю" className="max-w-full max-h-[85vh] rounded-2xl object-contain shadow-2xl" />
            <button
              onClick={() => setPreviewImage(null)}
              className="absolute -top-10 right-0 text-white p-2 rounded-full bg-slate-800/80"
            >
              <X size={20} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;