# İlk etkileşimli Linux oturumunda GiysunPASS şifre seçimini açar.
case $- in
  *i*)
    if [ -t 0 ] && [ ! -f /var/lib/giysunpass-install/complete ]; then
      printf '\nGiysunPASS ilk kurulum: panel şifrenizi seçin.\n'
      if [ "$(id -u)" -eq 0 ]; then
        /usr/local/sbin/giysunpass-kur || printf 'Tekrar denemek için: sudo giysunpass-kur\n'
      else
        sudo /usr/local/sbin/giysunpass-kur || printf 'Tekrar denemek için: sudo giysunpass-kur\n'
      fi
    fi
    ;;
esac
