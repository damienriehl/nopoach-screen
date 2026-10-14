'use strict';
// In-page exhibit navigation remains usable without JavaScript.
document.addEventListener('DOMContentLoaded', () => {
  const links = [...document.querySelectorAll('.exhibits a')];
  if (links.length) {
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        for (const link of links) {
          if (link.hash === '#' + entry.target.id) link.setAttribute('aria-current', 'location');
          else link.removeAttribute('aria-current');
        }
      }
    }, {rootMargin: '-5% 0px -65% 0px'});
    links.forEach(link => {
      const section = document.getElementById(link.hash.slice(1));
      if (section) observer.observe(section);
    });
  }
  function revealRecord() {
    const element = document.getElementById(location.hash.slice(1));
    if (element && element.tagName === 'DETAILS') element.open = true;
  }
  window.addEventListener('hashchange', revealRecord);
  revealRecord();
});
