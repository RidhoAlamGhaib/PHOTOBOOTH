<!doctype html>
{{-- Opened by the NEO PHOTO booth app in a hidden browser; not for people. --}}
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="csrf-token" content="{{ csrf_token() }}">
<meta name="robots" content="noindex">
<title>NEO PHOTO booth bridge</title>
</head>
<body>
<p>NEO PHOTO booth bridge</p>
<script>
window.__neo = {};
window.neoCall = async function (id, path, payload, apiKey) {
    try {
        const token = document.querySelector('meta[name="csrf-token"]').content;
        const r = await fetch(path, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRF-TOKEN': token,
                'X-Api-Key': apiKey
            },
            body: JSON.stringify(payload)
        });
        const text = await r.text();
        let data = null;
        try { data = JSON.parse(text); } catch (e) {}
        window.__neo[id] = {status: r.status, data: data, text: data ? null : text.slice(0, 300)};
    } catch (e) {
        window.__neo[id] = {status: 0, error: String(e)};
    }
};
window.NEO_BRIDGE = true;
</script>
</body>
</html>
