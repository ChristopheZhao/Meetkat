import { NavLink, Outlet } from "react-router";

import styles from "./root.module.css";

function ShellFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Meetkat · 决策会议室</p>
          <h1 className={styles.title}>
            实时多智能体协同决策室
            <span className={styles.titleAccent}>Central MAS</span>
          </h1>
        </div>
        <nav className={styles.nav}>
          <NavLink
            className={({ isActive }) =>
              isActive ? styles.activeLink : styles.link
            }
            to="/"
          >
            房间列表
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
        <p className={styles.fallbackEyebrow}>正在恢复房间状态</p>
        <h2 className={styles.fallbackTitle}>等待事件流权威快照加载</h2>
        <p className={styles.fallbackCopy}>
          房间界面始终绑定到 journal-backed 读模型，首次渲染会先等真实数据，不用本地占位猜测。
        </p>
      </section>
    </ShellFrame>
  );
}
