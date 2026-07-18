<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="{$charset}" />
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{if $kbarticle.title}{$kbarticle.title} - {/if}{$pagetitle} - {$companyname}</title>

    {include file="$template/includes/head.tpl"}

    {$headoutput}

</head>
<body data-phone-cc-input="{$phoneNumberInputStyle}">
{if $captcha}{$captcha->getMarkup()}{/if}
{$headeroutput}

<section id="header">
    <div class="container">
        <ul class="top-nav">
            {if $languagechangeenabled && count($locales) > 1}
                <li>
                    <a href="#" class="choose-language" data-toggle="popover" id="languageChooser">
                        {$activeLocale.localisedName}
                        <b class="caret"></b>
                    </a>
                    <div id="languageChooserContent" class="hidden">
                        <ul>
                            {foreach $locales as $locale}
                                <li>
                                    <a href="{$currentpagelinkback}language={$locale.language}">{$locale.localisedName}</a>
                                </li>
                            {/foreach}
                        </ul>
                    </div>
                </li>
            {/if}
            {if $loggedin}
                <li>
                    <a href="#" data-toggle="popover" id="accountNotifications" data-placement="bottom">
                        {$LANG.notifications}
                        {if count($clientAlerts) > 0}
                            <span class="label label-info">{lang key='notificationsnew'}</span>
                        {/if}
                        <b class="caret"></b>
                    </a>
                    <div id="accountNotificationsContent" class="hidden">
                        <ul class="client-alerts">
                        {foreach $clientAlerts as $alert}
                            <li>
                                <a href="{$alert->getLink()}">
                                    <i class="fas fa-fw fa-{if $alert->getSeverity() == 'danger'}exclamation-circle{elseif $alert->getSeverity() == 'warning'}exclamation-triangle{elseif $alert->getSeverity() == 'info'}info-circle{else}check-circle{/if}"></i>
                                    <div class="message">{$alert->getMessage()}</div>
                                </a>
                            </li>
                        {foreachelse}
                            <li class="none">
                                {$LANG.notificationsnone}
                            </li>
                        {/foreach}
                        </ul>
                    </div>
                </li>
                <li class="primary-action">
                    <a href="{$WEB_ROOT}/logout.php" class="btn">
                        {$LANG.clientareanavlogout}
                    </a>
                </li>
            {else}
                <li>
                    <a href="{$WEB_ROOT}/clientarea.php">{$LANG.login}</a>
                </li>
                {if $condlinks.allowClientRegistration}
                    <li>
                        <a href="{$WEB_ROOT}/register.php">{$LANG.register}</a>
                    </li>
                {/if}
                <li class="primary-action">
                    <a href="{$WEB_ROOT}/cart.php?a=view" class="btn">
                        {$LANG.viewcart}
                    </a>
                </li>
            {/if}
            {if $adminMasqueradingAsClient || $adminLoggedIn}
                <li>
                    <a href="{$WEB_ROOT}/logout.php?returntoadmin=1" class="btn btn-logged-in-admin" data-toggle="tooltip" data-placement="bottom" title="{if $adminMasqueradingAsClient}{$LANG.adminmasqueradingasclient} {$LANG.logoutandreturntoadminarea}{else}{$LANG.adminloggedin} {$LANG.returntoadminarea}{/if}">
                        <i class="fas fa-sign-out-alt"></i>
                    </a>
                </li>
            {/if}
        </ul>

        <a href="{$WEB_ROOT}/index.php" class="logo"><img src="{$WEB_ROOT}/templates/six_ffc/img/ffc-logo-banner.webp" alt="{$companyname}"></a>

    </div>
</section>

<section id="main-menu">

    <nav id="nav" class="navbar navbar-default navbar-main" role="navigation">
        <div class="container">
            <!-- Brand and toggle get grouped for better mobile display -->
            <div class="navbar-header">
                <button type="button" class="navbar-toggle" data-toggle="collapse" data-target="#primary-nav">
                    <span class="sr-only">{lang key='toggleNav'}</span>
                    <span class="icon-bar"></span>
                    <span class="icon-bar"></span>
                    <span class="icon-bar"></span>
                </button>
            </div>

            <!-- Collect the nav links, forms, and other content for toggling -->
            <div class="collapse navbar-collapse" id="primary-nav">

                <ul class="nav navbar-nav">

                    {include file="$template/includes/navbar.tpl" navbar=$primaryNavbar}

                </ul>

                <ul class="nav navbar-nav navbar-right">

                    {include file="$template/includes/navbar.tpl" navbar=$secondaryNavbar}

                </ul>

            </div><!-- /.navbar-collapse -->
        </div>
    </nav>

</section>

{if $templatefile == 'homepage'}
    <section id="home-banner">
        <div class="container text-center">
            <h2>Free websites, domains &amp; email for charities</h2>
            <p class="lead" style="margin:15px auto 25px;max-width:760px;">
                Free For Charity helps verified 501(c)(3) nonprofits get online at no cost &mdash; first a <strong>website on GitHub Pages</strong>, built and validated; then a <strong>free .org domain</strong> once your site is proven; then <strong>Microsoft&nbsp;365 or Google Workspace email</strong> &mdash; one gated onboarding journey.
            </p>
            <p style="margin-bottom:30px;">
                <a href="https://freeforcharity.org/501c3/" class="btn btn-primary btn-lg" style="margin:5px;">501(c)(3) charity onboarding &raquo;</a>
                <a href="https://freeforcharity.org/pre501c3/" class="btn btn-primary btn-lg" style="margin:5px;">Pre-501(c)(3) / new nonprofit &raquo;</a>
            </p>
            {* Domain search now lives on freeforcharity.org/domains (RDAP availability +
               near-peer/SEO checks, routing to the free .org product) instead of the WHMCS
               domainchecker.php box, which sent charities down the self-serve registrar path. *}
            <p style="margin-bottom:12px;">
                <a href="https://freeforcharity.org/domains/#check-your-domain" class="btn btn-lg" style="margin:5px; background-color:#ffffff; color:#2a6682; font-weight:600;">Check if your .org is available &raquo;</a>
            </p>
            <p style="font-size:14px; opacity:.9; margin-bottom:0;">
                We check availability and warn about confusing .com/.net look-alikes. Check names any time &mdash; we register your .org in Cloudflare for free once your website is live and validated. No self-serve registrar checkout.
            </p>
        </div>
    </section>
    <div class="home-shortcuts">
        <div class="container">
            <div class="row">
                <div class="col-md-4 hidden-sm hidden-xs text-center">
                    <p class="lead">
                        The charity journey
                    </p>
                </div>
                <div class="col-sm-12 col-md-8">
                    <ul>
                        <li>
                            <a id="btnGetStarted" href="https://freeforcharity.org/501c3/">
                                <i class="fas fa-flag-checkered"></i>
                                <p>
                                    Get started &amp; onboard <span>&raquo;</span>
                                </p>
                            </a>
                        </li>
                        <li>
                            <a id="btnMyServices" href="{$WEB_ROOT}/clientarea.php">
                                <i class="fas fa-briefcase"></i>
                                <p>
                                    My services <span>&raquo;</span>
                                </p>
                            </a>
                        </li>
                        <li>
                            <a id="btnKnowledgebase" href="{$WEB_ROOT}/knowledgebase.php">
                                <i class="fas fa-book"></i>
                                <p>
                                    Knowledgebase <span>&raquo;</span>
                                </p>
                            </a>
                        </li>
                        <li>
                            <a id="btnGetSupport" href="{$WEB_ROOT}/submitticket.php">
                                <i class="far fa-envelope"></i>
                                <p>
                                    Get support <span>&raquo;</span>
                                </p>
                            </a>
                        </li>
                    </ul>
                </div>
            </div>
        </div>
    </div>
{/if}

{include file="$template/includes/validateuser.tpl"}
{include file="$template/includes/verifyemail.tpl"}

<section id="main-body">
    <div class="container{if $skipMainBodyContainer}-fluid without-padding{/if}">
        <div class="row">

        {if !$inShoppingCart && ($primarySidebar->hasChildren() || $secondarySidebar->hasChildren())}
            {if $primarySidebar->hasChildren() && !$skipMainBodyContainer}
                <div class="col-md-9 pull-md-right">
                    {include file="$template/includes/pageheader.tpl" title=$displayTitle desc=$tagline showbreadcrumb=true}
                </div>
            {/if}
            <div class="col-md-3 pull-md-left sidebar">
                {include file="$template/includes/sidebar.tpl" sidebar=$primarySidebar}
            </div>
        {/if}
        <!-- Container for main page display content -->
        <div class="{if !$inShoppingCart && ($primarySidebar->hasChildren() || $secondarySidebar->hasChildren())}col-md-9 pull-md-right{else}col-xs-12{/if} main-content">
            {if !$primarySidebar->hasChildren() && !$showingLoginPage && !$inShoppingCart && $templatefile != 'homepage' && !$skipMainBodyContainer}
                {include file="$template/includes/pageheader.tpl" title=$displayTitle desc=$tagline showbreadcrumb=true}
            {/if}
