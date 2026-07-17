<?php

/**
 * ffc_promote_charity_record.php  —  FFC onboarding → client-record copy hook
 * =============================================================================
 *
 * PURPOSE
 *   FFC runs WHMCS as a charity CRM: each charity's reusable profile lives once
 *   at the CLIENT level (Configuration → Custom Client Fields), and every order
 *   references it. This hook keeps that record populated automatically. When an
 *   onboarding order is ACCEPTED (approved), it copies the answers the charity
 *   gave on the onboarding product form into the matching client custom fields,
 *   so the charity never re-enters them and the footer-config bridge can read
 *   the whole footer dataset from one GetClientsDetails call.
 *
 *       pid 16  = Pre-501(c)(3) Charity Onboarding
 *       pid 33  = 501(c)(3) Charity Onboarding
 *
 *   Fields copied (public footer / identity data ONLY):
 *       onboarding field (slug or legacy name)  →  client custom field
 *       ------------------------------------------------------------------
 *       ein / "...EIN..."                        →  EIN (IRS tax ID)
 *       mission / "...mission..."                →  Brief mission statement
 *       guidestar-public / "...GuideStar URL..." →  Candid (GuideStar) profile URL
 *       guidestar-full                           →  Candid direct / shared profile link
 *       public-phone                             →  Public phone (website footer)
 *       public-email                             →  Public email (website footer)
 *       footer-location                          →  Public city & state (website footer)
 *       facebook-page / "Charity Facebook Page"  →  Facebook Page URL
 *       linkedin-page / "Charity LinkedIn Page"  →  LinkedIn organization page URL
 *       social-instagram                         →  Instagram URL
 *       social-x                                 →  X (Twitter) URL
 *       social-youtube                           →  YouTube URL
 *       (client account Company Name)            →  Legal organization name
 *
 *   PII IS NEVER COPIED. The onboarding form also collects board members' and
 *   the primary/technical contacts' INDIVIDUAL LinkedIn / phone / email. Those
 *   are private and are deliberately excluded — only the explicit allowlist
 *   above (the charity's PUBLIC footer values) is ever promoted.
 *
 * COPY-IF-EMPTY (idempotent, self-service-safe)
 *   A client field is only written when it is currently EMPTY. This makes the
 *   hook idempotent (re-accepting an order changes nothing) and means it never
 *   clobbers a value the charity later edited themselves in their portal.
 *
 * FAIL-SAFE GUARANTEE (this hook runs during order acceptance)
 *   Every path is wrapped in try/catch. On ANY exception, missing data, or
 *   unresolved field id it simply does nothing and returns — it never throws,
 *   so it can never disrupt order acceptance. The worst a bug here can do is
 *   fail to pre-fill a field, which an admin or the charity then fills by hand.
 *
 * HOW TO DISABLE / ROLL BACK
 *   Delete this single file:
 *       public_html/hub/includes/hooks/ffc_promote_charity_record.php
 *   WHMCS auto-discovers hooks by presence in that directory; removing the file
 *   removes the behavior instantly, no config change or restart.
 *
 * SOURCE OF TRUTH / DEPLOYMENT
 *   Version-controlled here (canonical):
 *       whmcs/hooks/ffc_promote_charity_record.php
 *     in FreeForCharity/FFC-Cloudflare-Automation.
 *   Deployed to production WHMCS at:
 *       public_html/hub/includes/hooks/ffc_promote_charity_record.php
 *   Edit here, review via PR, run `php -l`, then redeploy over FTPS. Never edit
 *   on the server.
 *
 * WHMCS API NOTES (verified against WHMCS 8.x developer docs)
 *   - Action point: AcceptOrder. Fires when an order is accepted (from the admin
 *     UI or the AcceptOrder API action, which is how the triage runner approves
 *     $0 onboarding orders). $vars['orderid'] is the accepted order id.
 *   - An order's provisioned products are rows in tblhosting where
 *     orderid = <orderid>; each row has id (the service id), userid (client id)
 *     and packageid (the product/pid).
 *   - A product's custom field DEFINITIONS are tblcustomfields rows with
 *     type='product', relid=<pid>. The SUBMITTED values are tblcustomfieldsvalues
 *     rows with fieldid=<field id> and relid=<service id>.
 *   - Client custom field DEFINITIONS are tblcustomfields with type='client';
 *     their values are tblcustomfieldsvalues with relid=<client id>.
 *   - Client field ids are resolved by NAME at runtime (LIKE), never hardcoded,
 *     so the hook survives renumbering. Results cached per request.
 *
 * @refs FreeForCharity/FFC-Cloudflare-Automation#697
 */

use WHMCS\Database\Capsule;

if (!defined('WHMCS')) {
    die('This file cannot be accessed directly');
}

add_hook('AcceptOrder', 1, function ($vars) {
    // Onboarding product ids whose answers feed the client record.
    $ONBOARDING_PIDS = [16, 33];

    try {
        $orderId = isset($vars['orderid']) ? (int) $vars['orderid'] : 0;
        if ($orderId <= 0) {
            return; // Nothing to do.
        }

        // --- mapping: canonical key -> LIKE fragment identifying the client field ---
        // Fragments are chosen to hit exactly one client field. e.g. the two
        // Candid fields are 'Candid (GuideStar) profile URL' and
        // 'Candid direct / shared profile link'; 'profile url' hits only the
        // first, 'direct' only the second.
        $clientFieldLike = [
            'ein'               => 'ein',
            'mission'           => 'brief mission',
            'guidestar-profile' => 'profile url',
            'guidestar-direct'  => 'direct',
            'public-phone'      => 'public phone',
            'public-email'      => 'public email',
            'footer-location'   => 'city',
            'facebook'          => 'facebook',
            'linkedin'          => 'linkedin organization',
            'instagram'         => 'instagram',
            'x'                 => 'x (twitter)',
            'youtube'           => 'youtube',
            'legal-name'        => 'legal organization name',
        ];

        // Resolve a client custom field id by NAME (LIKE), cached per request.
        static $clientFieldIdCache = [];
        $resolveClientFieldId = function ($likeFragment) use (&$clientFieldIdCache) {
            $key = strtolower($likeFragment);
            if (array_key_exists($key, $clientFieldIdCache)) {
                return $clientFieldIdCache[$key];
            }
            $id = null;
            try {
                $row = Capsule::table('tblcustomfields')
                    ->where('type', 'client')
                    ->whereRaw('LOWER(fieldname) LIKE ?', ['%' . $key . '%'])
                    ->orderBy('id')
                    ->first();
                if ($row !== null && isset($row->id)) {
                    $id = (int) $row->id;
                }
            } catch (\Throwable $e) {
                $id = null;
            }
            $clientFieldIdCache[$key] = $id;
            return $id;
        };

        // Given an onboarding field name ("slug|Label" or a legacy plain label),
        // return the canonical mapping key, or null if the field must NOT be
        // copied (board/primary/technical PII, AI/timezone/attestation, etc.).
        $canonicalKey = function ($rawName) {
            $name = (string) $rawName;
            $slug = '';
            $bar = strpos($name, '|');
            if ($bar !== false) {
                $slug = strtolower(trim(substr($name, 0, $bar)));
            }
            $slugMap = [
                'ein'              => 'ein',
                'mission'          => 'mission',
                'guidestar-public' => 'guidestar-profile',
                'guidestar-full'   => 'guidestar-direct',
                'public-phone'     => 'public-phone',
                'public-email'     => 'public-email',
                'footer-location'  => 'footer-location',
                'facebook-page'    => 'facebook',
                'linkedin-page'    => 'linkedin',
                'social-instagram' => 'instagram',
                'social-x'         => 'x',
                'social-youtube'   => 'youtube',
            ];
            if ($slug !== '' && isset($slugMap[$slug])) {
                return $slugMap[$slug];
            }
            // Legacy plain-named fields (pid 16). Precise matches only so board
            // members' 'LinkedIn'/'phone'/'email' PII is never picked up.
            // Word-boundary matches for 'ein'/'mission' so an unrelated field
            // containing the letters (e.g. "being") can never mis-map.
            $lower = strtolower($name);
            if (preg_match('/\bein\b/', $lower)) {
                return 'ein';
            }
            if (preg_match('/\bmission\b/', $lower)) {
                return 'mission';
            }
            if (strpos($lower, 'guidestar') !== false || strpos($lower, 'candid') !== false) {
                return 'guidestar-profile';
            }
            if (strpos($lower, 'charity facebook') !== false) {
                return 'facebook';
            }
            if (strpos($lower, 'charity linkedin') !== false) {
                return 'linkedin';
            }
            return null; // Not an allowlisted public field → never copied.
        };

        // Write a value into a client field only if that field is currently
        // empty (copy-if-empty). Returns true if it wrote.
        $copyIfEmpty = function ($clientFieldId, $clientId, $value) {
            $value = trim((string) $value);
            if ($clientFieldId === null || $clientId <= 0 || $value === '') {
                return false;
            }
            $existing = Capsule::table('tblcustomfieldsvalues')
                ->where('fieldid', $clientFieldId)
                ->where('relid', $clientId)
                ->first();
            if ($existing !== null) {
                if (trim((string) $existing->value) !== '') {
                    return false; // Already set — never clobber self-service edits.
                }
                Capsule::table('tblcustomfieldsvalues')
                    ->where('fieldid', $clientFieldId)
                    ->where('relid', $clientId)
                    ->update(['value' => $value]);
                return true;
            }
            Capsule::table('tblcustomfieldsvalues')->insert([
                'fieldid' => $clientFieldId,
                'relid'   => $clientId,
                'value'   => $value,
            ]);
            return true;
        };

        // The onboarding services provisioned by this order.
        $services = Capsule::table('tblhosting')
            ->where('orderid', $orderId)
            ->whereIn('packageid', $ONBOARDING_PIDS)
            ->get();

        foreach ($services as $service) {
            $serviceId = (int) $service->id;
            $pid = (int) $service->packageid;
            $clientId = (int) $service->userid;
            if ($serviceId <= 0 || $clientId <= 0) {
                continue;
            }

            // Copy the account Company Name → Legal organization name.
            try {
                $client = Capsule::table('tblclients')->where('id', $clientId)->first();
                if ($client !== null && isset($client->companyname)) {
                    $legalId = $resolveClientFieldId($clientFieldLike['legal-name']);
                    $copyIfEmpty($legalId, $clientId, $client->companyname);
                }
            } catch (\Throwable $e) {
                // ignore — pre-fill only
            }

            // Product field definitions for this pid (id → fieldname).
            $prodFields = Capsule::table('tblcustomfields')
                ->where('type', 'product')
                ->where('relid', $pid)
                ->get();

            foreach ($prodFields as $pf) {
                $key = $canonicalKey($pf->fieldname);
                if ($key === null || !isset($clientFieldLike[$key])) {
                    continue; // Not an allowlisted field.
                }

                // The submitted value for this product field on this service.
                $valRow = Capsule::table('tblcustomfieldsvalues')
                    ->where('fieldid', (int) $pf->id)
                    ->where('relid', $serviceId)
                    ->first();
                if ($valRow === null) {
                    continue;
                }

                $clientFieldId = $resolveClientFieldId($clientFieldLike[$key]);
                $copyIfEmpty($clientFieldId, $clientId, $valRow->value);
            }
        }
    } catch (\Throwable $e) {
        // FAIL SAFE: never let a pre-fill error disrupt order acceptance.
        return;
    }
});
