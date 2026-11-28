<?php
// Add these lines at the end of routes/web.php (keep the "use" line at the top of that file).

use App\Http\Controllers\BoothController;

Route::get('/booth/bridge', [BoothController::class, 'bridge']);
Route::post('/booth/session', [BoothController::class, 'session'])->middleware('throttle:120,1,booth-session');
Route::post('/booth/promo', [BoothController::class, 'promo'])->middleware('throttle:60,1,booth-promo');
Route::post('/booth/coupon', [BoothController::class, 'coupon'])->middleware('throttle:60,1,booth-coupon');
Route::post('/booth/coupon/use', [BoothController::class, 'couponUse'])->middleware('throttle:60,1,booth-coupon-use');

// Remote HP: the guest shoots from their phone (BoothRemoteController).
// throttle:N,1,name = own counter per route (without a name every throttled route
// shares one counter per IP, and the phone polling once a second would use it up).
use App\Http\Controllers\BoothRemoteController;

$rk = '[A-Za-z0-9_-]{24,64}';
Route::post('/booth/remote/open', [BoothRemoteController::class, 'open'])->middleware('throttle:60,1,rm-open');
Route::post('/booth/remote/sync', [BoothRemoteController::class, 'sync'])->middleware('throttle:900,1,rm-sync');
Route::post('/booth/remote/close', [BoothRemoteController::class, 'close'])->middleware('throttle:60,1,rm-close');
Route::get('/r/{key}', [BoothRemoteController::class, 'page'])->where('key', $rk)
    ->middleware('throttle:120,1,rm-page');
Route::get('/r/{key}/state', [BoothRemoteController::class, 'state'])->where('key', $rk)
    ->middleware('throttle:900,1,rm-state');
Route::post('/r/{key}/cmd', [BoothRemoteController::class, 'cmd'])->where('key', $rk)
    ->middleware('throttle:180,1,rm-cmd');
Route::get('/r/{key}/photo/{n}', [BoothRemoteController::class, 'photo'])->where('key', $rk)
    ->whereNumber('n')->middleware('throttle:900,1,rm-photo');
