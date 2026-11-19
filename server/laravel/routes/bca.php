<?php
// BCA SNAP QRIS: paste these lines at the end of routes/web.php (keep the "use" line at the top).
// BCA posts without a CSRF token, so these two routes skip the CSRF check
// (also add 'openapi/*' to the CSRF exceptions, see README-BCA-QRIS.md).

use App\Http\Controllers\BcaQrisController;

Route::post('/openapi/v1.0/access-token/b2b', [BcaQrisController::class, 'token'])
    ->withoutMiddleware(['App\Http\Middleware\VerifyCsrfToken', 'Illuminate\Foundation\Http\Middleware\ValidateCsrfToken',
                         'Illuminate\Foundation\Http\Middleware\PreventRequestForgery'])
    ->middleware('throttle:60,1');
Route::post('/openapi/v1.0/qr/qr-mpm-notify', [BcaQrisController::class, 'notify'])
    ->withoutMiddleware(['App\Http\Middleware\VerifyCsrfToken', 'Illuminate\Foundation\Http\Middleware\ValidateCsrfToken',
                         'Illuminate\Foundation\Http\Middleware\PreventRequestForgery'])
    ->middleware('throttle:300,1');
