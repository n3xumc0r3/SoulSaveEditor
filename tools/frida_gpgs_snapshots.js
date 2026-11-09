// frida_gpgs_snapshots.js — list Google Play Saved Games snapshots of the
// logged-in Google account from inside the Soul Knight process.
//
// Why from the game process: the OAuth scope
//   oauth2:https://www.googleapis.com/auth/games
// is only minted for OAuth clients registered for GPGS; the game's own
// signature (package com.ChillyRoom.DungeonShooter) is one of them, so we
// ask GoogleAuthUtil for a token while running inside the game.
//
// Usage (rooted device/emulator with frida-server running):
//   frida -U -f com.ChillyRoom.DungeonShooter -l frida_gpgs_snapshots.js
// or attach to the already-running game:
//   frida -U com.ChillyRoom.DungeonShooter -l frida_gpgs_snapshots.js
//
// Expected output: JSON with every snapshot (name like GPV2_<uid>),
// its description and lastModifiedTimestamp. That answers:
//   * which save slots live in GPGS for this account,
//   * WHEN each was written (dual-write discriminator: if the timestamp
//     refreshes after the game syncs on 8.7.0, the legacy channel is alive).

function log(s) { console.log(s); }

Java.perform(function () {
    var tries = 0;

    function run() {
        tries++;
        var ActivityThread = Java.use('android.app.ActivityThread');
        var ctx = ActivityThread.currentApplication();
        if (!ctx || !ctx.getApplicationContext()) {
            if (tries < 30) { setTimeout(run, 1000); return; }
            log('[!] no application context'); return;
        }
        var app = ctx.getApplicationContext();

        // 1. find the primary Google account on the device
        var AccountManager = Java.use('android.accounts.AccountManager');
        var am = AccountManager.get(app);
        var accounts = am.getAccounts();
        var google = null;
        for (var i = 0; i < accounts.length; i++) {
            if (accounts[i].type.value === 'com.google') { google = accounts[i]; break; }
        }
        if (!google) { log('[!] no com.google account on device'); return; }
        log('[*] account: ' + google.name.value);

        // 2. mint a token for the games scope
        var GoogleAuthUtil = Java.use('com.google.android.gms.auth.GoogleAuthUtil');
        var scopes = [
            'oauth2:https://www.googleapis.com/auth/games',
            'androidmarket'                       // legacy fallback, some builds accept
        ];
        var token = null, used = null;
        for (var s = 0; s < scopes.length && !token; s++) {
            try {
                token = GoogleAuthUtil.getToken(app, google, scopes[s]).toString();
                used = scopes[s];
            } catch (e) {
                log('[-] scope failed: ' + scopes[s] + ' -> ' + e.toString().substring(0, 200));
            }
        }
        if (!token) { log('[!] no token obtained'); return; }
        log('[+] token OK via: ' + used);
        log('[TOKEN] ' + token);

        // 3. list snapshots in-process
        try {
            var URL = Java.use('java.net.URL');
            var conn = URL.$new('https://www.googleapis.com/games/v1/players/me/snapshots?maxResults=25&language=en').openConnection();
            conn.setRequestProperty('Authorization', 'Bearer ' + token);
            conn.setRequestProperty('Accept', 'application/json');
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(15000);
            var code = conn.getResponseCode();
            log('[*] HTTP ' + code);
            var is = (code >= 400 ? conn.getErrorStream() : conn.getInputStream());
            var body = Java.use('java.util.Scanner').$new(is, 'UTF-8').useDelimiter('\\A').next();
            log('--- snapshots list ---');
            log(body);
            log('--- end ---');
            // friendly hint: grep the JSON for "GPV2_" and lastModifiedTimestamp
        } catch (e) {
            log('[!] http failed: ' + e);
            log('(token is printed above — use it from a PC: ' +
                'curl -H "Authorization: Bearer <TOKEN>" ' +
                'https://www.googleapis.com/games/v1/players/me/snapshots)');
        }
    }

    setTimeout(run, 8000);   // let GMS bindings settle after spawn
});
