
                </div><!-- /.main-content -->
                {if !$inShoppingCart && $secondarySidebar->hasChildren()}
                    <div class="col-md-3 pull-md-left sidebar sidebar-secondary">
                        {include file="$template/includes/sidebar.tpl" sidebar=$secondarySidebar}
                    </div>
                {/if}
            <div class="clearfix"></div>
        </div>
    </div>
</section>

<section id="footer">
    <div class="container">
        <a href="#" class="back-to-top"><i class="fas fa-chevron-up"></i></a>
        <p class="text-center">
            <a href="https://freeforcharity.org/">Free For Charity</a> &mdash; free websites, domains &amp; email for verified 501(c)(3) nonprofits.
            &nbsp;|&nbsp; <a href="https://freeforcharity.org/501c3/">Get started</a>
            &nbsp;|&nbsp; <a href="{$WEB_ROOT}/knowledgebase.php">Knowledgebase</a>
            &nbsp;|&nbsp; <a href="{$WEB_ROOT}/contact.php">Contact us</a>
        </p>
        <p>{lang key="copyrightFooterNotice" year=$date_year company=$companyname}</p>
    </div>
</section>

<div id="fullpage-overlay" class="hidden">
    <div class="outer-wrapper">
        <div class="inner-wrapper">
            <img src="{$WEB_ROOT}/assets/img/overlay-spinner.svg">
            <br>
            <span class="msg"></span>
        </div>
    </div>
</div>

<div class="modal system-modal fade" id="modalAjax" tabindex="-1" role="dialog" aria-hidden="true">
    <div class="modal-dialog">
        <div class="modal-content panel-primary">
            <div class="modal-header panel-heading">
                <button type="button" class="close" data-dismiss="modal">
                    <span aria-hidden="true">&times;</span>
                    <span class="sr-only">{$LANG.close}</span>
                </button>
                <h4 class="modal-title"></h4>
            </div>
            <div class="modal-body panel-body">
                {$LANG.loading}
            </div>
            <div class="modal-footer panel-footer">
                <div class="pull-left loader">
                    <i class="fas fa-circle-notch fa-spin"></i>
                    {$LANG.loading}
                </div>
                <button type="button" class="btn btn-default" data-dismiss="modal">
                    {$LANG.close}
                </button>
                <button type="button" class="btn btn-primary modal-submit">
                    {$LANG.submit}
                </button>
            </div>
        </div>
    </div>
</div>

{include file="$template/includes/generate-password.tpl"}

{$footeroutput}

{* Intake-funnel counter (issue FFC-Cloudflare-Automation#684): counts order-form
   views vs completions via a consent-free beacon — daily counters only, no
   cookies or identifiers. Endpoint: freeforcharity.org/api/funnel-beacon.php *}
{literal}<script>
(function () {
  if (!navigator.sendBeacon || location.pathname.indexOf('/cart.php') === -1) return;
  var q = new URLSearchParams(location.search);
  var a = q.get('a');
  if (a === 'add' && /^[0-9]{1,4}$/.test(q.get('pid') || '')) {
    navigator.sendBeacon('/api/funnel-beacon.php?s=view&p=' + q.get('pid'));
  } else if (a === 'complete') {
    navigator.sendBeacon('/api/funnel-beacon.php?s=complete');
  }
})();
</script>{/literal}
</body>
</html>
