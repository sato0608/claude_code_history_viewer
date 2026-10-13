// 通知の表示と、通知をクリックしたときに画面を開くための Service Worker。
// 表示内容は常に最新の履歴を見せたいので、ページやデータのキャッシュは行わない。
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const sid = (event.notification.data || {}).session;
  const url = sid ? `/#session=${encodeURIComponent(sid)}` : "/";
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      // すでに開いている画面があれば前に出して、そのセッションを開かせる
      if ("focus" in w) {
        w.postMessage({ type: "open-session", session: sid });
        return w.focus();
      }
    }
    return self.clients.openWindow(url);
  })());
});
