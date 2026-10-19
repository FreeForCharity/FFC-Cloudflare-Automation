<?php

/**
 * ffc_require_company.php  —  FFC "Company Name is required" hook
 * =============================================================================
 *
 * PURPOSE
 *   WHMCS has no setting that makes the client Company Name required: General
 *   Settings -> Other lists only name, address and phone as optional-able, and
 *   Company Name is always optional. Applicants often skip it, and then their
 *   application is hard to find (the charity-lane workflows key on the org
 *   name; epic #1618). FFC serves only organizations, so every client needs one.
 *   This hook refuses a blank Company Name:
 *     - ClientDetailsValidation: registration and client-area profile edits;
 *     - ShoppingCartValidateCheckout: checkout, for a new account (the posted
 *       company name) and for an existing client whose record has none.
 *
 * FAIL-OPEN GUARANTEE (this runs on EVERY registration and order)
 *   Every path is wrapped in try/catch and returns [] (allow) on any exception
 *   or unexpected input. It is read-only: no writes, no network calls. The worst
 *   a bug here can do is let a blank Company Name through, never block a
 *   charity that filled it in.
 *
 * HOW TO DISABLE / ROLL BACK
 *   Delete this single file:
 *       public_html/hub/includes/hooks/ffc_require_company.php
 *   WHMCS discovers hooks by presence in that directory; removing the file
 *   removes the behavior instantly, with no config change or restart.
 *
 * SOURCE OF TRUTH / DEPLOYMENT
 *   Canonical copy: whmcs/hooks/ffc_require_company.php in
 *   FreeForCharity/FFC-Cloudflare-Automation. Edit here, review via PR, run
 *   `php -l`, then upload over FTPS (see whmcs/hooks/README.md). Never edit on
 *   the server.
 */

if (!defined('WHMCS')) {
    die('This file cannot be accessed directly');
}

const FFC_REQUIRE_COMPANY_MESSAGE =
    "Please enter your organization's name in the Company Name field. "
    . 'We use it to find your application and to name your website.';

/**
 * The validation error for a company name, or null when it is filled in.
 */
function ffc_require_company_error($companyName)
{
    if (!is_scalar($companyName) && $companyName !== null) {
        return null; // unexpected shape: fail open
    }
    return trim((string) $companyName) === '' ? FFC_REQUIRE_COMPANY_MESSAGE : null;
}

add_hook('ClientDetailsValidation', 1, function ($vars) {
    try {
        if (!is_array($vars) || !array_key_exists('companyname', $vars)) {
            return [];
        }
        $error = ffc_require_company_error($vars['companyname']);
        return $error === null ? [] : [$error];
    } catch (\Throwable $e) {
        return [];
    }
});

add_hook('ShoppingCartValidateCheckout', 1, function ($vars) {
    try {
        if (!is_array($vars)) {
            return [];
        }
        $existing = ($vars['custtype'] ?? '') === 'existing';
        if (!$existing && array_key_exists('companyname', $vars)) {
            $company = $vars['companyname'];
        } elseif (!empty($vars['userid'])) {
            $existing = true;
            $company = \WHMCS\Database\Capsule::table('tblclients')
                ->where('id', (int) $vars['userid'])
                ->value('companyname');
        } else {
            return [];
        }
        $error = ffc_require_company_error($company);
        if ($error === null) {
            return [];
        }
        if ($existing) {
            $error .= ' Existing account: add it under Account -> Your Profile, then check out again.';
        }
        return [$error];
    } catch (\Throwable $e) {
        return [];
    }
});
