export const metadata = {
  title: "Tech Admin — Project Bestie",
  description: "System administration and technical maintenance dashboard.",
};

import AdminSidebar from "../components/AdminSidebar";

export default function AdminLayout({ children }) {
  return (
    <div className="flex min-h-screen">
      <AdminSidebar />
      <main className="flex-1 ml-64 min-h-screen bg-[var(--bg-primary)]">
        {children}
      </main>
    </div>
  );
}
