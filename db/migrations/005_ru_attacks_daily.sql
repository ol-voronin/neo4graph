-- Ukrainian attacks on Russia as reported by the Russian MoD (claims of intercepted drones/missiles), per Moscow-time day.
create table if not exists ru_attacks_daily (
  date date primary key,
  drones int not null default 0,
  missiles int not null default 0,
  posts int not null default 0,
  source text not null default 'tg:mod_russia'
);
