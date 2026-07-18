<?php

/**
 * ffc_status_product_match.php  —  FFC checkout-validation hook
 * =============================================================================
 *
 * PURPOSE
 *   Steers applicants who selected the WRONG onboarding product for their IRS
 *   status to the correct one, BEFORE an order is placed. Two onboarding
 *   products exist:
 *       pid 16  = Pre-501(c)(3) Charity Onboarding   (not yet IRS-approved)
 *       pid 33  = 501(c)(3) Charity Onboarding       (IRS-approved 501(c)(3))
 *   Each product asks a "What is the legal status of your organization?"
 *   custom field. If the answer contradicts the product chosen, checkout is
 *   blocked with a friendly message pointing at the right product. This keeps
 *   us collecting the right onboarding information for each applicant.
 *
 * FAIL-OPEN GUARANTEE (safety is paramount — this hook runs on EVERY order)
 *   A checkout hook that throws or misbehaves can break ALL orders for ALL
 *   applicants. Therefore this hook is deliberately fail-OPEN: every code path
 *   is wrapped in try/catch, and on ANY exception, missing session data,
 *   unreadable custom field, or unresolved field id it returns [] (an empty
 *   array), which allows the order to proceed. It is read-only: no writes, no
 *   external/network calls. The worst a bug here can do is fail to catch a
 *   mis-filed order — never block a legitimate charity.
 *
 * HOW TO DISABLE / ROLL BACK
 *   Delete this single file:
 *       public_html/hub/includes/hooks/ffc_status_product_match.php
 *   WHMCS auto-discovers hooks by presence in that directory; removing the file
 *   removes the behavior instantly, with no config change or restart.
 *
 * SOURCE OF TRUTH / DEPLOYMENT
 *   Version-controlled here (canonical):
 *       whmcs/hooks/ffc_status_product_match.php
 *     in FreeForCharity/FFC-Cloudflare-Automation.
 *   Deployed to production WHMCS at:
 *       public_html/hub/includes/hooks/ffc_status_product_match.php
 *   Edit here, review via PR, then redeploy over FTPS. Never edit on the server.
 *
 * WHMCS API NOTES (verified against WHMCS 8.x developer docs)
 *   - Action point: ShoppingCartValidateCheckout. Fires server-side during
 *     checkout completion, before order/invoice creation. Returning a non-empty
 *     array of error strings (or a single string) blocks checkout and displays
 *     those messages to the applicant. Returning [] allows the order.
 *   - Cart shape: the products in the cart are read from
 *     $_SESSION['cart']['products']. Each entry is an associative array with a
 *     'pid' (product id) and a 'customfields' map. ASSUMPTION (documented per
 *     WHMCS 8.x behavior): 'customfields' is keyed by the custom field's
 *     numeric id => submitted value. If that shape is ever absent or different,
 *     the guarded reads below simply skip and the hook falls through to [].
 *   - Field ids differ per product (on pid 16 the legal-status field is id 3;
 *     on pid 33 it is id 106). We do NOT hardcode these: we resolve the id by
 *     NAME via a Capsule query on tblcustomfields (relid = pid, fieldname LIKE
 *     '%legal status%'), so the hook survives field renumbering. Result cached
 *     per pid for the duration of the request.
 *
 * @refs FreeForCharity/FFC-Cloudflare-Automation#697 #678
 */

use WHMCS\Database\Capsule;

if (!defined('WHMCS')) {
    die('This file cannot be accessed directly');
}

add_hook('ShoppingCartValidateCheckout', 1, function ($vars) {
    // Onboarding product ids (see header).
    $PRE_501C3_PID = 16;
    $FULL_501C3_PID = 33;

    $errors = [];

    // Per-request cache of the resolved legal-status field id, keyed by pid.
    static $fieldIdCache = [];

    /**
     * Resolve the numeric custom-field id for the "legal status" question on a
     * given product, by NAME (survives field renumbering). Returns null if it
     * cannot be resolved — callers treat null as "cannot validate → allow".
     */
    $resolveLegalStatusFieldId = function ($pid) use (&$fieldIdCache) {
        if (array_key_exists($pid, $fieldIdCache)) {
            return $fieldIdCache[$pid];
        }
        $fieldId = null;
        try {
            $row = Capsule::table('tblcustomfields')
                ->where('type', 'product')
                ->where('relid', $pid)
                ->where('fieldname', 'like', '%legal status%')
                ->first();
            if ($row !== null && isset($row->id)) {
                $fieldId = (int) $row->id;
            }
        } catch (\Throwable $e) {
            // Fail open: unresolved field id → no validation for this product.
            $fieldId = null;
        }
        $fieldIdCache[$pid] = $fieldId;
        return $fieldId;
    };

    try {
        // No cart / unexpected shape → nothing to validate, allow the order.
        if (
            empty($_SESSION['cart']['products']) ||
            !is_array($_SESSION['cart']['products'])
        ) {
            return [];
        }

        foreach ($_SESSION['cart']['products'] as $product) {
            if (!is_array($product) || !isset($product['pid'])) {
                continue;
            }
            $pid = (int) $product['pid'];

            // Only the two onboarding products are relevant.
            if ($pid !== $PRE_501C3_PID && $pid !== $FULL_501C3_PID) {
                continue;
            }

            $fieldId = $resolveLegalStatusFieldId($pid);
            if ($fieldId === null) {
                continue; // Cannot resolve the question → allow.
            }

            // customfields is assumed keyed by fieldid => value (WHMCS 8.x).
            $customfields =
                isset($product['customfields']) && is_array($product['customfields'])
                    ? $product['customfields']
                    : [];
            if (!array_key_exists($fieldId, $customfields)) {
                continue; // No answer captured for this field → allow.
            }

            $status = strtolower(trim((string) $customfields[$fieldId]));
            if ($status === '') {
                continue; // Blank answer → allow.
            }

            // Classify the answer. "pre" / "not yet determined" markers win over
            // a full-501c3 match, so an ambiguous answer errs toward allowing.
            $isPre =
                strpos($status, 'pre') !== false ||
                strpos($status, 'not yet') !== false ||
                strpos($status, 'not-yet') !== false ||
                strpos($status, 'determined') !== false ||
                strpos($status, 'pending') !== false ||
                strpos($status, 'applying') !== false;

            $mentions501 = strpos($status, '501') !== false;
            $mentionsC3 =
                strpos($status, '(3)') !== false ||
                strpos($status, 'c3') !== false ||
                strpos($status, 'c(3)') !== false;
            $isFull = $mentions501 && $mentionsC3 && !$isPre;

            if ($pid === $PRE_501C3_PID && $isFull) {
                // Ordered Pre-501(c)(3) but says they ARE an approved 501(c)(3).
                $errors[] =
                    'You indicated your organization is an approved 501(c)(3). ' .
                    'Please use the 501(c)(3) Charity Onboarding product instead of the ' .
                    'Pre-501(c)(3) one so we collect the right information. Need help? Contact us.';
            } elseif ($pid === $FULL_501C3_PID && $isPre) {
                // Ordered 501(c)(3) but says they are NOT yet approved.
                $errors[] =
                    'You indicated your organization is not yet an approved 501(c)(3). ' .
                    'Please use the Pre-501(c)(3) Charity Onboarding product instead of the ' .
                    '501(c)(3) one so we collect the right information. Need help? Contact us.';
            }
        }
    } catch (\Throwable $e) {
        // FAIL OPEN: never let a hook error block a legitimate charity's order.
        return [];
    }

    return $errors;
});
