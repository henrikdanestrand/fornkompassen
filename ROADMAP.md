# Fornkompassen - Roadmap

Fas 1–5 (kärnmotor, användarkonton, mobilt fältläge, produktionssättning) är
klara och driftsatta. Det här dokumentet beskriver nästa fas: funktioner och
UX-förbättringar utöver grundfunktionen, i den ordning de naturligt bygger på
varandra.

## Fas 6: Fältfunktioner & UX-förfining

### 6.1 Snabba UX-vinster
Låg insats, ingen inverkan på övriga delar - naturligt att börja här.

- [ ] **Ångra istället för bekräfta** - byt blockerande `confirm()`-dialoger
      (t.ex. vid radering av GCP-punkt) mot en toast med en "Ångra"-knapp.
- [ ] **Sökruta för plats** - hoppa till en adress/ort istället för att bara
      panorera manuellt eller vänta på GPS.
- [ ] **Avståndsmätning** - mät avstånd mellan två punkter på kartan.
- [ ] **Ljust/högkontrastläge** - ett läge anpassat för starkt dagsljus
      utomhus, som komplement till dagens mörka tema.

### 6.2 Fältfunktioner
Bygger vidare på GPS-/fältläget från Fas 3.

- [ ] **Kompassriktning** - visa enhetens riktning (gyroskop/kompass-API).
- [ ] **Lista över fornlämningar nära GPS-position** - komplement till
      dagens klick-på-kartan mot RAÄ:s WMS-lager.
- [ ] **Offline-cachning** - cacha redan nedladdade/inpassade kartor så
      appen fungerar utan uppkoppling i skogen. Störst praktisk nytta för
      fältarbete, men störst teknisk insats i den här fasen - gör klart
      6.1 och stabilisera övrigt gränssnitt först.

### 6.3 Samarbete & annotering
Ritverktyget är grunden - kommentarer och foton hänger på samma
markörsystem, så bygg dem i den ordningen.

- [ ] **Ritverktyg** - markera egna upptäckter direkt på kartan.
- [ ] **Kommentarer/anteckningar per plats** - kopplat till markörerna ovan.
- [ ] **Foto-bilaga** - ta en bild i fält, nåla fast den vid en markör.
- [ ] **Dela karta via länk** - en publik, läsbar vy av en specifik inpassad
      karta att skicka till en kollega.

### 6.4 Historisk jämförelse
- [ ] **Tidsskjutreglage** - bläddra mellan olika års inpassade
      bakgrundskartor på samma plats (bygger vidare på de samtidiga
      bakgrundslagren från Fas 2).

### 6.5 Polish (gör sist, när funktionsutbudet satt sig)
- [ ] **Onboarding** - kort guidad genomgång av GCP-punkt-arbetsflödet för
      nya användare.
- [ ] **Tillgänglighetsgranskning** - tangentbordsnavigering, kontrast på
      markörfärger för färgblinda, ARIA-etiketter.

## Kända begränsningar att komma ihåg

- **Flygfoto (Lantmäteriet STAC-bild)** är pausad/dold - "Ortofoto
  Nedladdning" tillåter bara bruk "inom familjekretsen", oförenligt med en
  publik flera-användare-app. Kräver ett annat licensspår innan den kan
  återaktiveras.
- **Historiska Lantmäteriet-kartor** (Ekonomiska kartan m.fl.) finns fritt
  på `ftp://download-opendata.lantmateriet.se/` men kräver egen
  georefererings-pipeline (RT90-transform från `.tfw`-filer) och ett sätt
  att hitta rätt kartblad - inte påbörjat.
- **Render-backend** (gratisnivå) somnar efter ~15 min utan trafik, ~30-60
  sek uppvakningstid vid nästa anrop.
