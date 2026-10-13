// Cloudflare Worker "neo-booth" on booth.neophotoindonesia.my.id.
// Relays the booth's two API calls to the hosting, so the host's Imunify360
// anti-bot only sees Cloudflare's Workers IP (whitelisted there) instead of
// every outlet's changing IP. One Worker serves all booths.
export default {
  async fetch(req) {
    const u = new URL(req.url);
    // Only the two booth files are relayed; anything else is refused.
    if (!/^\/booth_(session|promo)\.php$/.test(u.pathname)) {
      return new Response("Not found", { status: 404 });
    }
    u.hostname = "neophotoindonesia.my.id";
    return fetch(new Request(u, req));
  },
};
