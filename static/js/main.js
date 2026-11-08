

// 1) Ask "Are you sure?" before any form that has a data-confirm message
//    (used for Delete and Return buttons).
document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
        if (!confirm(form.dataset.confirm)) {
            event.preventDefault();      // user pressed Cancel: stop the form
        }
    });
});

// 2) Success messages goes away after 6 seconds (errors stay until the page changes).
document.querySelectorAll(".flash-success").forEach(function (box) {
    setTimeout(function () { box.style.display = "none"; }, 6000);
});
