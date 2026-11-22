/* Dark-mode toggle, shared across pages. Loaded in <head> to avoid flash-of-light. */
(function () {
  /* Coupe toutes les transitions le temps d'un rendu.
     Sans ca, un element dont le fond vient d'une variable de theme (background:
     var(--bg-2)) ET qui porte une transition sur `background` reste FIGE sur la couleur
     de l'ancien theme jusqu'au rechargement : la transition demarre avant que la nouvelle
     valeur de variable soit resolue. Symptome constate : bouton d'edition charbon avec
     glyphe noir en theme clair (contraste 1.38, crayon invisible), et en-tetes de colonnes
     illisibles. Concerne .btn-edit, .toggle-btn, #search, .theme-toggle, th…
     Traiter le probleme ici couvre aussi les regles ajoutees plus tard. */
  function sansTransition(fn) {
    var root = document.documentElement;
    root.classList.add("theme-switching");
    fn();
    // Forcer un reflow pour que la coupure soit prise en compte avant de reactiver.
    void root.offsetHeight;
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        root.classList.remove("theme-switching");
      });
    });
  }

  function apply(theme) {
    if (theme === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.textContent = theme === "dark" ? "☀" : "🌙";
      btn.title = theme === "dark" ? "Passer en clair" : "Passer en sombre";
    }
  }

  var stored = null;
  try {
    stored = localStorage.getItem("theme");
  } catch (e) {}
  // Apply immediately (html element exists during head parsing) — no FOUC.
  apply(stored === "dark" ? "dark" : "light");

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    var current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    apply(current);
    btn.addEventListener("click", function () {
      var isDark = document.documentElement.getAttribute("data-theme") === "dark";
      var next = isDark ? "light" : "dark";
      try {
        localStorage.setItem("theme", next);
      } catch (e) {}
      sansTransition(function () { apply(next); });
    });
  });
})();
