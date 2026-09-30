SET SESSION sql_mode=(SELECT REPLACE(@@sql_mode,'ANSI_QUOTES',''));
-- Harbours added through the manager UI after the initial seed, captured here
-- so `docker compose down -v` no longer loses them. Sourced by init.sql after
-- Student1_InsertData_Initial.sql.
--
-- Student1 owns the Office rows, so these live under Student1/ and use their
-- own generated OfficeIDs -- none of them collide with the O1..O5 that
-- Student1_InsertData_Initial.sql inserts, and Student2 must still not insert
-- offices at all.
--
-- These harbours have no boats of their own. That is a real state the app
-- handles: the booking page marks them "No boats yet", and the manager offices
-- page offers "Stock with demo boats" for exactly this case.

INSERT INTO Office (OfficeID, Street, Country, City, ZIP) VALUES
("Ofa3ec8c4", "Marina Aktau, Microdistrict 1", "Kazakhstan", "Aktau", "130000"),
("Oa2fca785", "Sixhaven Marina, Nieuwendammerkade 4", "Netherlands", "Amsterdam", "1022 AB"),
("Oec7c9d05", "Marina Zeas, Akti Themistokleous", "Greece", "Athens", "18537"),
("O5c35d1e0", "Westhaven Marina, Westhaven Drive", "New Zealand", "Auckland", "1010"),
("Od6fdacb6", "Nyhavn 1", "Denmark", "Copenhagen", "1051"),
("O962cda15", "City Sporthafen, Vorsetzen 50", "Germany", "Hamburg", "20459"),
("O6570b750", "Katajanokan Marina, Luotsikatu 1", "Finland", "Helsinki", "00160"),
("O1ee9e222", "Marina do Funchal, Avenida do Mar", "Portugal", "Madeira", "9000-055"),
("Ocb0c7373", "Port de Soller, Carrer de Santa Caterina", "Spain", "Mallorca", "07108"),
("O7c3eb8c1", "Pier 39, Beach Street", "United States", "San Francisco", "94133"),
("O7bfc8553", "Marina San Antonio, Passeig de la Mar", "Spain", "Sant Antoni", "07820"),
("O35c8e412", "Wasahamnen, Djurgardsbrunnsvagen 12", "Sweden", "Stockholm", "11521"),
("Obae73495", "Darling Harbour, Wheat Road", "Australia", "Sydney", "2000"),
("O739e89fe", "Yumenoshima Marina, Koto City", "Japan", "Tokyo", "136-0081"),
("O7a629913", "Chaffers Marina, Herd Street", "New Zealand", "Wellington", "6011");

