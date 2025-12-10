// Questo file contiene logiche ausiliarie per lo scanner.
// La logica principale è inline nel template per accedere alle variabili Jinja.

console.log("Scanner library loaded ready for Panificio Manager.");

// Gestione permessi camera (opzionale, html5-qrcode lo fa in automatico)
document.addEventListener('DOMContentLoaded', () => {
    // Controlla se siamo su HTTPS o localhost, altrimenti avvisa
    if (location.hostname !== "localhost" && location.hostname !== "127.0.0.1" && location.protocol !== 'https:') {
        alert("ATTENZIONE: La fotocamera richiede HTTPS per funzionare sui dispositivi mobili. Assicurati di accedere tramite HTTPS o localhost.");
    }
});