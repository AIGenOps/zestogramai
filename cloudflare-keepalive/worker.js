export default {
  // Triggered by Cloudflare Cron Trigger (every 10 minutes)
  async scheduled(event, env, ctx) {
    const targetUrl = "https://zestogram-bot.onrender.com";
    try {
      const response = await fetch(targetUrl, {
        method: "GET",
        headers: {
          "User-Agent": "CloudflareWorker-KeepAlive/1.0"
        }
      });
      console.log(`[KeepAlive] Pinged ${targetUrl} - HTTP ${response.status}`);
    } catch (error) {
      console.error(`[KeepAlive] Error pinging ${targetUrl}:`, error);
    }
  },

  // Manual trigger / status check via browser or HTTP request
  async fetch(request, env, ctx) {
    const targetUrl = "https://zestogram-bot.onrender.com";
    try {
      const startTime = Date.now();
      const response = await fetch(targetUrl, {
        method: "GET",
        headers: {
          "User-Agent": "CloudflareWorker-KeepAlive/1.0"
        }
      });
      const responseText = await response.text();
      const latencyMs = Date.now() - startTime;

      return new Response(
        JSON.stringify(
          {
            status: "ok",
            target: targetUrl,
            upstreamStatus: response.status,
            latencyMs: latencyMs,
            upstreamResponse: responseText
          },
          null,
          2
        ),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json; charset=utf-8"
          }
        }
      );
    } catch (error) {
      return new Response(
        JSON.stringify(
          {
            status: "error",
            target: targetUrl,
            error: String(error)
          },
          null,
          2
        ),
        {
          status: 502,
          headers: {
            "Content-Type": "application/json; charset=utf-8"
          }
        }
      );
    }
  }
};
