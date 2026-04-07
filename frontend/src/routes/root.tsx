import { NavLink, Outlet } from "react-router";

import styles from "./root.module.css";

function ShellFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Decision Room</p>
          <h1 className={styles.title}>Real-Time Multi-Agent Meeting Room</h1>
        </div>
        <nav className={styles.nav}>
          <NavLink
            className={({ isActive }) =>
              isActive ? styles.activeLink : styles.link
            }
            to="/"
          >
            Rooms
          </NavLink>
        </nav>
      </header>
      <main className={styles.main}>{children}</main>
    </div>
  );
}

export function RootLayout() {
  return (
    <ShellFrame>
      <Outlet />
    </ShellFrame>
  );
}

export function RootHydrateFallback() {
  return (
    <ShellFrame>
      <section className={styles.fallbackCard}>
        <p className={styles.fallbackEyebrow}>Hydrating room state</p>
        <h2 className={styles.fallbackTitle}>
          Loading the authoritative room snapshot.
        </h2>
        <p className={styles.fallbackCopy}>
          Room UI stays bound to the journal-backed read model, so the first
          render waits for the data-router state instead of guessing from local
          placeholders.
        </p>
      </section>
    </ShellFrame>
  );
}
