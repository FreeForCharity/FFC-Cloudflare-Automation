/**
 * clone-enhance.js — the entire client-side runtime of an FFC static clone.
 *
 * A captured WordPress site arrives carrying ~1.9 MB of jQuery, jQuery Migrate,
 * Underscore, the Divi theme bundle, Divi Plus, Swiper, Hustle, MediaElement,
 * a cookie-consent plugin and two analytics beacons. Measured against the DOM
 * that was actually captured for viewpointministriesinternational.org — 589
 * pages, class tokens counted outside <style> and <script> — that payload
 * exists to serve exactly two behaviours:
 *
 *   .et_pb_menu / .mobile_menu_bar   589 pages   a hamburger menu
 *   .et-waypoint                     589 pages   a scroll-in fade
 *
 * Everything else Divi ships a handler for — toggles, accordions, tabs,
 * sliders, video overlays, counters, countdowns, lightboxes, contact forms —
 * appears on **zero** pages. The sliders and the gallery appear on one page
 * each and degrade to a plain list. So the vendored bundles are replaced by
 * this file rather than trimmed: it is smaller than the smallest of them,
 * it has no dependencies, and it reaches no network.
 *
 * Three properties are deliberate and worth keeping if this is ever edited:
 *
 *   - **No JavaScript is required to read the site.** The capture strips
 *     Divi's `.et-waypoint{opacity:0}` rule, so content is painted whether or
 *     not this file loads. Animation is enhancement; visibility never is.
 *   - **Nothing is built from a string.** No innerHTML, no document.write, no
 *     eval, no Function, no injected <script>. The mobile menu is a
 *     cloneNode() of markup the server already sent, which is why no attacker-
 *     controlled text can become markup here.
 *   - **Nothing leaves the page.** No fetch, no beacon, no third-party host.
 *     The analytics and consent plugins were removed together: the banner
 *     existed to ask permission for the trackers, and the trackers reported to
 *     a property this site no longer owns.
 */
(function () {
  'use strict';

  /** Modules that can own a hamburger: the Divi menu module and its fullwidth twin. */
  var MENU_MODULE = '.et_pb_menu, .et_pb_fullwidth_menu';

  /**
   * Wire one menu module's hamburger.
   *
   * Divi ships the toggle as a bare `<span>` and builds the mobile list in
   * JavaScript. Both are reproduced, with the accessibility defects fixed
   * rather than reproduced: the span becomes a real button to the accessibility
   * tree, it carries its expanded state, and it answers Enter and Space.
   */
  function wireMenu(module) {
    var bar = module.querySelector('.mobile_menu_bar');
    var nav = module.querySelector('.mobile_nav');
    var source = module.querySelector('.et_pb_menu__menu ul.et-menu, .et-menu-nav ul.et-menu');
    if (!bar || !nav || !source) return;

    // Built once, on first open. A menu nobody opens costs nothing.
    var list = null;
    var open = false;

    bar.setAttribute('role', 'button');
    bar.setAttribute('tabindex', '0');
    bar.setAttribute('aria-label', 'Toggle menu');
    bar.setAttribute('aria-expanded', 'false');

    function build() {
      if (list) return list;
      // cloneNode, not innerHTML: the markup is copied as a parsed tree, so
      // there is no string for anything to be injected into.
      list = source.cloneNode(true);
      list.removeAttribute('id');
      list.className = 'et_mobile_menu';
      // The clone carries the desktop menu's ids; a duplicate id is invalid and
      // breaks in-page anchors and label targeting.
      var ids = list.querySelectorAll('[id]');
      for (var i = 0; i < ids.length; i += 1) ids[i].removeAttribute('id');
      list.style.display = 'none';
      nav.appendChild(list);
      return list;
    }

    function setOpen(next) {
      var el = build();
      open = next;
      el.style.display = next ? 'block' : 'none';
      bar.setAttribute('aria-expanded', next ? 'true' : 'false');
      nav.classList.toggle('opened', next);
      nav.classList.toggle('closed', !next);
    }

    bar.addEventListener('click', function (event) {
      event.preventDefault();
      setOpen(!open);
    });

    bar.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
      event.preventDefault();
      setOpen(!open);
    });

    // Escape closes, and focus goes back to the control that opened it —
    // otherwise a keyboard user is stranded inside a list they cannot dismiss.
    module.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape' && event.key !== 'Esc') return;
      if (!open) return;
      setOpen(false);
      bar.focus();
    });
  }

  /**
   * Reveal Divi's scroll-in elements.
   *
   * Divi hides `.et-waypoint` at `opacity: 0` and reveals it from a scroll
   * handler. The capture removes that hiding rule, so this only adds the class
   * that starts the keyframe animation the stylesheet already defines. If
   * IntersectionObserver is missing, every element is revealed immediately —
   * the failure mode is "no animation", never "no content".
   */
  function wireWaypoints() {
    var targets = document.querySelectorAll('.et-waypoint:not(.et-animated)');
    if (!targets.length) return;

    if (typeof window.IntersectionObserver !== 'function') {
      for (var i = 0; i < targets.length; i += 1) targets[i].classList.add('et-animated');
      return;
    }

    var observer = new window.IntersectionObserver(
      function (entries) {
        for (var j = 0; j < entries.length; j += 1) {
          if (!entries[j].isIntersecting) continue;
          entries[j].target.classList.add('et-animated');
          observer.unobserve(entries[j].target);
        }
      },
      { rootMargin: '0px 0px -10% 0px' },
    );
    for (var k = 0; k < targets.length; k += 1) observer.observe(targets[k]);
  }

  function init() {
    var modules = document.querySelectorAll(MENU_MODULE);
    for (var i = 0; i < modules.length; i += 1) wireMenu(modules[i]);
    wireWaypoints();
  }

  // `defer` already guarantees a parsed document, but this file is also correct
  // if some future step inlines it in <head>.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
