<?php
// Add these lines at the end of routes/web.php (keep the "use" line at the top of that file).

use App\Http\Controllers\BoothController;

Route::get('/booth/bridge', [BoothController::class, 'bridge']);
Route::post('/booth/session', [BoothController::class, 'session'])->middleware('throttle:120,1');
Route::post('/booth/promo', [BoothController::class, 'promo'])->middleware('throttle:60,1');
Route::post('/booth/coupon', [BoothController::class, 'coupon'])->middleware('throttle:60,1');
